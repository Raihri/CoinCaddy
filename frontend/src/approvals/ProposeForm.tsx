import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import type { Account } from "../auth/session";
import { parseMajorToMinor } from "../money";
import type { FieldErrors } from "../settings/apiErrors";
import { errorFor } from "../settings/apiErrors";
import { Field } from "../settings/fields";
import type { Obligation } from "../obligations/types";
import { placeErrors, textProblem, type Proposal } from "./types";

const PROPOSE_FIELDS = ["obligation_id", "channel", "message", "amount_minor", "from_account_id", "payment_review_id", "readiness_check_id", "expires_in_hours"];

type Kind = Proposal["kind"];
type ReadinessRow = { id: string; product_name: string; state: string; created_at: string; run_id: string; product_id: string };
type ReviewRow = { id: string; decision: string; as_of: string; request: { account_id: string; amount_minor: number; payee: string } };

const KIND_OPTIONS: { value: Kind; label: string; tier: string }[] = [
  { value: "supplier_payment", label: "Pay supplier (sandbox)", tier: "T3" },
  { value: "financing_application", label: "Submit financing application (sandbox lender)", tier: "T3" },
];

// Proposes an exact action. The client only names the source record; the server builds the payload from the records.
export function ProposeForm({ base, accounts, money, exponent, canReadReviews, canReadReadiness, onCreated, onCancel }: {
  base: string; accounts: Account[]; money: (minor: number, currency: string) => string; exponent: (currency: string) => number;
  canReadReviews: boolean; canReadReadiness: boolean; onCreated: (p: Proposal) => void; onCancel: () => void;
}) {
  const [kind, setKind] = useState<Kind>("supplier_payment");
  const [invoices, setInvoices] = useState<Obligation[]>([]);
  const [bills, setBills] = useState<Obligation[]>([]);
  const [checks, setChecks] = useState<ReadinessRow[]>([]);
  const [reviews, setReviews] = useState<ReviewRow[]>([]);
  const [f, setF] = useState({ obligation: "", channel: "email", message: "", amount: "", account: "", review: "", check: "", hours: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => { setF((x) => ({ ...x, [k]: v })); setErrors((e) => ({ ...e, [k]: "", _form: "" })); };

  useEffect(() => {
    api<{ items: Obligation[] }>(`${base}/obligations`).then((r) => {
      if (!r.ok) return;
      const live = r.data.items.filter((o) => (o.status === "open" || o.status === "partially_settled") && !o.disputed);
      setInvoices(live.filter((o) => o.direction === "receivable" && o.overdue));
      setBills(live.filter((o) => o.direction === "payable"));
    });
    if (canReadReadiness) api<ReadinessRow[]>(`${base}/financing-readiness`).then((r) => { if (r.ok) setChecks(latestReady(r.data)); });
    if (canReadReviews) api<ReviewRow[]>(`${base}/payment-reviews`).then((r) => { if (r.ok) setReviews(r.data); });
  }, [base, canReadReadiness, canReadReviews]);

  const payable = accounts.filter((a) => a.kind !== "cash");
  const bill = bills.find((b) => b.id === f.obligation);
  const invoice = invoices.find((i) => i.id === f.obligation);
  const tier = KIND_OPTIONS.find((k) => k.value === kind)!.tier;
  const maxHours = tier === "T3" ? 24 : 72;

  const pickInvoice = (id: string) => {
    const inv = invoices.find((i) => i.id === id);
    setF((x) => ({ ...x, obligation: id, message: inv && !x.message ? `Dear ${inv.party.name}, invoice ${inv.document_number} for ${money(inv.outstanding_minor, inv.currency)} was due on ${fmtDate(inv.due_date)}. Please arrange payment.` : x.message }));
    setErrors({});
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: FieldErrors = {};
    const body: Record<string, unknown> = { kind };
    if (f.hours) {
      const h = Number(f.hours);
      if (!Number.isInteger(h) || h < 1 || h > maxHours) errs.hours = `Enter whole hours from 1 to ${maxHours}.`;
      else body.expires_in_hours = h;
    }
    if (kind === "invoice_reminder") {
      if (!invoice) errs.obligation = "Choose an overdue invoice.";
      const m = textProblem(f.message, 10, 500, true);
      if (m) errs.message = m;
      Object.assign(body, { obligation_id: f.obligation, channel: f.channel, message: f.message });
    } else if (kind === "supplier_payment") {
      if (!bill) errs.obligation = "Choose a bill.";
      if (!f.account) errs.account = "Choose the account to pay from.";
      const acc = payable.find((a) => a.id === f.account);
      if (bill && acc && acc.currency !== bill.currency) errs.account = `This account is in ${acc.currency}; the bill is in ${bill.currency}.`;
      const amt = parseMajorToMinor(f.amount, exponent(bill?.currency ?? "BDT"), 1e15);
      if (!amt.ok) errs.amount = amt.error;
      else if (amt.minor <= 0) errs.amount = "Enter an amount above zero.";
      else if (bill && amt.minor > bill.outstanding_minor) errs.amount = `At most ${money(bill.outstanding_minor, bill.currency)} is outstanding.`;
      Object.assign(body, { obligation_id: f.obligation, from_account_id: f.account, amount_minor: amt.ok ? amt.minor : 0, payment_review_id: f.review || null });
    } else {
      if (!f.check) errs.check = "Choose a readiness check that is ready.";
      body.readiness_check_id = f.check;
    }
    if (Object.values(errs).some(Boolean)) return setErrors(errs);
    setBusy(true);
    const r = await api<Proposal>(`${base}/approvals`, "POST", body);
    setBusy(false);
    if (!r.ok) return setErrors(placeErrors(r.errors, PROPOSE_FIELDS));
    onCreated(r.data);
  };

  const amountMinor = bill ? parseMajorToMinor(f.amount, exponent(bill.currency), 1e15) : null;
  const matchingReviews = reviews.filter((r) => amountMinor?.ok && r.request.amount_minor === amountMinor.minor && r.request.account_id === f.account);

  return (
    <section className="card" aria-labelledby="propose-title">
      <div className="card-head">
        <h2 id="propose-title" className="section-title">New proposal</h2>
        <span className={`tier-chip tier-${tier}`}>{tier}</span>
      </div>
      <p className="muted small">
        {tier === "T3" ? "T3: an owner who did not propose it must approve, re-enter their password, and record how it was verified."
          : "T2: needs an explicit owner approval."} Nothing is sent or paid; approval only allows a later sandbox executor to act.
      </p>
      <p className="muted small">For an invoice reminder, <Link className="link" to="/receivables-guardian">create a verified-contact draft in Receivables Guardian</Link> first.</p>
      <form className="stack-form" onSubmit={submit} noValidate>
        <Field label="Action">
          <select value={kind} onChange={(e) => { setKind(e.target.value as Kind); setF((x) => ({ ...x, obligation: "", amount: "", review: "", check: "" })); setErrors({}); }}>
            {KIND_OPTIONS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
          </select>
        </Field>

        {kind === "invoice_reminder" && (
          <>
            <Field label="Overdue invoice" error={errorFor(errors, "obligation", "obligation_id")} hint={invoices.length ? undefined : "No overdue, undisputed invoice is outstanding."}>
              <select value={f.obligation} onChange={(e) => pickInvoice(e.target.value)}>
                <option value="">Choose…</option>
                {invoices.map((i) => <option key={i.id} value={i.id}>{i.document_number} · {i.party.name} · {money(i.outstanding_minor, i.currency)} · {i.days_overdue} days overdue</option>)}
              </select>
            </Field>
            <Field label="Channel">
              <select value={f.channel} onChange={(e) => set("channel", e.target.value)}>
                <option value="email">Email</option>
                <option value="sms">SMS</option>
              </select>
            </Field>
            <Field label="Message" error={errorFor(errors, "message")} hint={`${f.message.trim().length}/500. No links or account numbers.`}>
              <textarea rows={3} value={f.message} maxLength={600} onChange={(e) => set("message", e.target.value)} />
            </Field>
          </>
        )}

        {kind === "supplier_payment" && (
          <>
            <Field label="Bill" error={errorFor(errors, "obligation", "obligation_id")} hint={bills.length ? undefined : "No undisputed bill is outstanding."}>
              <select value={f.obligation} onChange={(e) => set("obligation", e.target.value)}>
                <option value="">Choose…</option>
                {bills.map((b) => <option key={b.id} value={b.id}>{b.document_number} · {b.party.name} · {money(b.outstanding_minor, b.currency)} outstanding</option>)}
              </select>
            </Field>
            <div className="form-grid">
              <Field label={`Amount${bill ? ` (${bill.currency})` : ""}`} error={errorFor(errors, "amount", "amount_minor")}>
                <input inputMode="decimal" value={f.amount} onChange={(e) => set("amount", e.target.value)} placeholder="60,000.00" />
              </Field>
              <Field label="Pay from" error={errorFor(errors, "account", "from_account_id")}>
                <select value={f.account} onChange={(e) => set("account", e.target.value)}>
                  <option value="">Choose…</option>
                  {payable.map((a) => <option key={a.id} value={a.id}>{a.label} ({a.currency})</option>)}
                </select>
              </Field>
            </div>
            {canReadReviews && (
              <Field label="Payment Review (optional)" error={errorFor(errors, "payment_review_id")}
                     hint={matchingReviews.length ? "Only reviews for this exact amount and account are listed." : "No saved review matches this amount and account."}>
                <select value={f.review} onChange={(e) => set("review", e.target.value)} disabled={!matchingReviews.length}>
                  <option value="">None</option>
                  {matchingReviews.map((r) => <option key={r.id} value={r.id}>{r.as_of} · {r.decision.replace(/_/g, " ")} · {r.request.payee}</option>)}
                </select>
              </Field>
            )}
          </>
        )}

        {kind === "financing_application" && (
          <Field label="Readiness check" error={errorFor(errors, "check", "readiness_check_id")}
                 hint={checks.length ? "Only the latest check of a product, and only when it is ready." : "No ready check yet. Run Financing Readiness on Funding Matches first."}>
            <select value={f.check} onChange={(e) => set("check", e.target.value)}>
              <option value="">Choose…</option>
              {checks.map((c) => <option key={c.id} value={c.id}>{c.product_name} · checked {c.created_at.slice(0, 10)}</option>)}
            </select>
          </Field>
        )}

        <Field label="Valid for (hours)" error={errorFor(errors, "hours", "expires_in_hours")} hint={`Default ${tier === "T3" ? 24 : 48}, at most ${maxHours}.`}>
          <input inputMode="numeric" value={f.hours} onChange={(e) => set("hours", e.target.value)} placeholder={tier === "T3" ? "24" : "48"} />
        </Field>
        {errors._form && <p className="badge badge-danger block-badge" role="alert">{errors._form}</p>}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Create proposal"}</button>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </section>
  );
}

function latestReady(rows: ReadinessRow[]): ReadinessRow[] {
  const seen = new Set<string>();
  const out: ReadinessRow[] = [];
  for (const r of rows) {  // newest first
    const key = `${r.run_id}:${r.product_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    if (r.state === "ready") out.push(r);
  }
  return out;
}

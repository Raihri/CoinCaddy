import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney, parseMajorToMinor } from "../money";
import type { FieldErrors } from "../settings/apiErrors";
import { exponentOf, Field } from "../settings/fields";
import type { Obligation } from "../obligations/types";

// Layout follows images/05-payment-review.png (proposed payment, four scenario check, evidence); styling follows design.md.
// The Rescue options section of the mockup belongs to a later prompt and is not built here.
type Decision = "within_reserve" | "reserve_shortfall" | "more_information_needed";
type Link_ = { kind: string; id: string; label: string };
type Path = {
  key: string; label: string; status: "within_reserve" | "shortfall" | "not_assessed";
  minimum: { date: string; amount_minor: number } | null;
  first_breach: { date: string; balance_minor: number; gap_minor: number } | null;
  shortfall_minor: number | null; funding_account_overdrawn: boolean;
  funding_account_minimum: { date: string; amount_minor: number } | null;
  assumptions: string[]; links: Link_[];
  events: { label: string; amount_minor: number; date: string; note: string | null; uncertain: boolean; source: string; source_id: string }[];
};
type Report = {
  id: string; as_of: string; created_at: string; created_by: string; decision: Decision; inputs_sha256: string; calc_version: string;
  results: {
    currency: string; floor_minor: number; reserve_defined: boolean; arithmetic_state: string; evidence_state: string; authorization: string;
    payment: { account_label: string; amount_minor: number; date: string; payee: string | null; linked_payable: { id: string; party: string; document_number: string } | null };
    paths: Path[]; blockers: { code: string; message: string; source_id: string | null }[]; notes: { code: string; message: string; source_id: string | null }[];
  };
};
type Row = { id: string; decision: Decision; created_at: string; payment: { amount_minor: number; date: string; payee: string | null; account_label: string }; currency: string };

const DECISION: Record<Decision, { label: string; cls: string }> = {
  within_reserve: { label: "Within reserve under stated assumptions", cls: "badge-success" },
  reserve_shortfall: { label: "Reserve shortfall", cls: "badge-danger" },
  more_information_needed: { label: "More information needed", cls: "badge-warning" },
};
const PATH_STATUS = { within_reserve: { label: "Within reserve", cls: "badge-success" }, shortfall: { label: "Shortfall", cls: "badge-danger" },
                      not_assessed: { label: "Not assessed", cls: "badge-muted" } } as const;

function sourceLink(kind: string, id: string): string | null {
  if (kind === "obligation") return `/records/obligations/${id}`;
  if (kind === "account") return `/records/accounts/${id}`;
  return null;
}

export function PaymentReviewPage() {
  const { active, profile, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}` : "";
  const canRun = active?.allowed_actions.includes("paymentreview.run") ?? false;
  const canRead = active?.allowed_actions.includes("paymentreview.read") ?? false;
  const [report, setReport] = useState<Report | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [payables, setPayables] = useState<Obligation[]>([]);
  const [loaded, setLoaded] = useState(false);
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));

  const loadRows = useCallback(async () => {
    if (!base || !canRead) { setLoaded(true); return; }
    const [l, p] = await Promise.all([api<Row[]>(`${base}/payment-reviews`), api<{ items: Obligation[] }>(`${base}/obligations?direction=payable`)]);
    if (p.ok) setPayables(p.data.items.filter((o) => o.status === "open" || o.status === "partially_settled"));
    if (l.ok) {
      setRows(l.data);
      if (l.data[0]) { const r = await api<Report>(`${base}/payment-reviews/${l.data[0].id}`); if (r.ok) setReport(r.data); }
    }
    setLoaded(true);
  }, [base, canRead]);
  useEffect(() => { loadRows(); }, [loadRows]);

  const open = async (id: string) => { const r = await api<Report>(`${base}/payment-reviews/${id}`); if (r.ok) setReport(r.data); };
  const saved = (r: Report) => { setReport(r); loadRows(); };

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Payment review</h1>
          <p className="page-sub">Check the payment, understand the gap, and see what could change the answer. This never pays anything.</p>
        </div>
      </div>
      {!canRead && <section className="card"><p className="muted">Only owners and bookkeepers can use Payment Review.</p></section>}
      {canRead && canRun && profile && <ReviewForm base={base} payables={payables} onSaved={saved} />}
      {canRead && loaded && !report && <section className="card" style={{ marginTop: 16 }}><p className="muted">No payment review has been saved yet. Enter a proposed payment above.</p></section>}
      {report && <ReportView report={report} money={money} />}
      {rows.length > 1 && (
        <section className="card" style={{ marginTop: 16 }} aria-labelledby="saved-title">
          <h2 id="saved-title" className="card-title">Saved reviews</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Saved</th><th>Payment</th><th>Date</th><th>Result</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="nowrap">{new Date(r.created_at).toLocaleString("en-GB")}</td>
                    <td className="nowrap">{money(r.payment.amount_minor, r.currency)}{r.payment.payee ? ` to ${r.payment.payee}` : ""}</td>
                    <td className="nowrap">{fmtDate(r.payment.date)}</td>
                    <td><span className={`badge ${DECISION[r.decision].cls}`}>{DECISION[r.decision].label}</span></td>
                    <td><button type="button" className="link-button" onClick={() => open(r.id)}>Open</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

function ReviewForm({ base, payables, onSaved }: { base: string; payables: Obligation[]; onSaved: (r: Report) => void }) {
  const { profile, options } = useSession();
  const accounts = profile?.accounts ?? [];
  const [f, setF] = useState({ account_id: accounts[0]?.id ?? "", amount: "", date: "", payee: "", linked: "", delay: "7", haircut: "0", extra: "", extra_date: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const account = accounts.find((a) => a.id === f.account_id);
  const currency = account?.currency ?? "BDT";
  const exp = exponentOf(options, currency);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((s) => ({ ...s, [k]: e.target.value }));
  const linkable = payables.filter((o) => o.currency === currency);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const amount = parseMajorToMinor(f.amount, exp, 1e15);
    if (!amount.ok) return setErrors({ amount_minor: amount.error });
    const extra = f.extra.trim() ? parseMajorToMinor(f.extra, exp, 1e15) : { ok: true as const, minor: 0 };
    if (!extra.ok) return setErrors({ extra: extra.error });
    if (!f.date) return setErrors({ payment_date: "Choose the payment date." });
    const body = { account_id: f.account_id, amount_minor: amount.minor, payment_date: f.date, payee: f.payee.trim() || null,
                   linked_payable_id: f.linked || null,
                   adverse: { receipt_delay_days: Number(f.delay) || 0, receipts_haircut_percent: Number(f.haircut) || 0,
                              extra_outflow_minor: extra.minor, extra_outflow_date: f.extra_date || null } };
    setBusy(true);
    const r = await api<Report>(`${base}/payment-reviews`, "POST", body);
    setBusy(false);
    if (!r.ok) return setErrors({ _form: r.message, ...r.errors });
    setErrors({});
    onSaved(r.data);
  };
  const linked = payables.find((o) => o.id === f.linked);
  return (
    <form className="card form-grid" onSubmit={submit} noValidate aria-label="Proposed payment" style={{ marginBottom: 16 }}>
      <h2 className="card-title span-2">Proposed payment</h2>
      {errors._form && <p className="badge badge-danger span-2" role="alert">{errors._form}</p>}
      <Field label="Funding account" error={errors.account_id}>
        <select value={f.account_id} onChange={(e) => { set("account_id")(e); setF((s) => ({ ...s, account_id: e.target.value, linked: "" })); }}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.label} · {a.currency}</option>)}
        </select>
      </Field>
      <Field label={`Amount (${currency})`} error={errors.amount_minor}><input inputMode="decimal" value={f.amount} onChange={set("amount")} placeholder="60,000.00" /></Field>
      <Field label="Payment date" error={errors.payment_date} hint="After today, within 13 weeks."><input type="date" value={f.date} onChange={set("date")} /></Field>
      <Field label="Payee (optional)" error={errors.payee}><input value={f.payee} maxLength={80} onChange={set("payee")} /></Field>
      <Field label="Linked bill (optional)" hint={linked ? `Replaces that bill's own payment in the forecast, so it is not counted twice.` : "Pick the bill this pays, if any."} error={errors.linked_payable_id}>
        <select value={f.linked} onChange={set("linked")}>
          <option value="">None: a new payment</option>
          {linkable.map((o) => <option key={o.id} value={o.id}>{o.party.name} · {o.document_number} · {formatMoney(o.outstanding_minor, o.currency, exp)} due {fmtDate(o.due_date)}</option>)}
        </select>
      </Field>
      <fieldset className="span-2 adverse-set">
        <legend>Your adverse case (at least one)</legend>
        <div className="form-grid">
          <Field label="Receipts arrive later by (days)" error={errors["adverse.receipt_delay_days"]}><input inputMode="numeric" value={f.delay} onChange={set("delay")} /></Field>
          <Field label="Receipts are lower by (%)" error={errors["adverse.receipts_haircut_percent"]}><input inputMode="numeric" value={f.haircut} onChange={set("haircut")} /></Field>
          <Field label={`Extra outflow (${currency})`} error={errors.extra}><input inputMode="decimal" value={f.extra} onChange={set("extra")} /></Field>
          <Field label="Extra outflow date" error={errors["adverse.extra_outflow_date"]}><input type="date" value={f.extra_date} onChange={set("extra_date")} /></Field>
        </div>
      </fieldset>
      <div className="form-actions span-2"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Reviewing…" : "Run four-scenario review"}</button></div>
    </form>
  );
}

function ReportView({ report, money }: { report: Report; money: (m: number, c: string) => string }) {
  const r = report.results;
  const cur = r.currency;
  const d = DECISION[report.decision];
  const p = r.payment;
  return (
    <>
      <section className="card review-head" aria-label="Review result">
        <div>
          <p className="muted small" style={{ margin: 0 }}>Proposed payment</p>
          <h2 className="section-title">{money(p.amount_minor, cur)}{p.payee ? ` to ${p.payee}` : ""}</h2>
        </div>
        <div className="review-meta">
          <span>From: {p.account_label}</span>
          <span>Payment date: {fmtDate(p.date)}</span>
          {p.linked_payable && <span>Pays: <Link className="link" to={`/records/obligations/${p.linked_payable.id}`}>{p.linked_payable.party} {p.linked_payable.document_number}</Link></span>}
        </div>
        <span className={`badge ${d.cls} review-badge`}>{d.label}</span>
      </section>

      {r.arithmetic_state === "reserve_shortfall" && r.evidence_state === "incomplete" &&
        <p className="badge badge-warning" style={{ marginTop: 12 }} role="status">A reserve shortfall is shown even though some evidence is incomplete.</p>}

      <div className="split split-even" style={{ marginTop: 16 }}>
        <section className="card" aria-labelledby="four-title">
          <h2 id="four-title" className="card-title">Four scenario check</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Scenario</th><th className="right">Lowest cash</th><th>First below reserve</th><th className="right">Shortfall</th><th>Result</th></tr></thead>
              <tbody>
                {r.paths.map((x) => (
                  <tr key={x.key}>
                    <th scope="row">{x.label}</th>
                    <td className="num">{x.minimum ? <>{money(x.minimum.amount_minor, cur)}<br /><span className="muted small">{fmtDate(x.minimum.date)}</span></> : "—"}</td>
                    <td className="nowrap">{x.first_breach ? fmtDate(x.first_breach.date) : x.status === "not_assessed" ? "—" : "None"}</td>
                    <td className="num">{x.shortfall_minor === null ? "—" : x.shortfall_minor ? money(x.shortfall_minor, cur) : "None"}</td>
                    <td><span className={`badge ${PATH_STATUS[x.status].cls}`}>{PATH_STATUS[x.status].label}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small" style={{ marginTop: 12 }}>
            Reserve floor {r.reserve_defined ? money(r.floor_minor, cur) : "not defined (zero used)"}. Within a day, money going out is counted before money coming in.
          </p>
        </section>

        <section className="card" aria-labelledby="evidence-title">
          <h2 id="evidence-title" className="card-title">Evidence that matters</h2>
          {r.blockers.length === 0 && r.notes.length === 0 && <p className="muted">No missing or stale evidence affects this result.</p>}
          {r.blockers.map((b) => (
            <div key={b.code + (b.source_id ?? "")} className="alert-card alert-medium">
              <div className="alert-title">Verify before relying on this</div>
              <p className="alert-cause">{b.message}</p>
              {b.source_id && b.code.includes("balance") && <Link className="chip" to={`/records/accounts/${b.source_id}`}>View supporting record</Link>}
              {b.source_id && b.code === "linked_payable_disputed" && <Link className="chip" to={`/records/obligations/${b.source_id}`}>View supporting record</Link>}
              {b.source_id && b.code === "unmatched_transfer" && <Link className="chip" to="/records/transfers">View supporting record</Link>}
            </div>
          ))}
          {r.notes.map((n) => (
            <div key={n.code + n.source_id} className="alert-card alert-low">
              <p className="alert-cause" style={{ margin: 0 }}>{n.message}</p>
              {n.code === "uncertain_receipt" && n.source_id && <Link className="chip" to={`/records/obligations/${n.source_id}`}>View supporting record</Link>}
            </div>
          ))}
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }} aria-labelledby="detail-title">
        <h2 id="detail-title" className="card-title">Assumptions and sources</h2>
        {r.paths.map((x) => (
          <details key={x.key} className="path-detail">
            <summary><strong>{x.label}</strong> <span className={`badge ${PATH_STATUS[x.status].cls}`}>{PATH_STATUS[x.status].label}</span></summary>
            <ul>{x.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            {x.funding_account_overdrawn && x.funding_account_minimum && (
              <p className="badge badge-danger">The funding account itself would be overdrawn to {money(x.funding_account_minimum.amount_minor, cur)} on {fmtDate(x.funding_account_minimum.date)}.</p>
            )}
            <div className="alert-records">
              <span className="muted small">Sources</span>
              {x.links.map((l) => { const to = sourceLink(l.kind, l.id); return to
                ? <Link key={l.kind + l.id} className="chip" to={to} title={l.id}>{l.label}</Link> : <span key={l.id} className="chip">{l.label}</span>; })}
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Date</th><th>Movement</th><th className="right">Amount</th></tr></thead>
                <tbody>
                  {x.events.map((e, i) => (
                    <tr key={i}><td className="nowrap">{fmtDate(e.date)}</td><td>{e.label}{e.uncertain ? " (uncertain)" : ""}{e.note ? <span className="muted small"> · {e.note}</span> : null}</td>
                        <td className="num">{money(e.amount_minor, cur)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
        <p className="muted small" style={{ marginTop: 12 }}>
          Calculated {new Date(report.created_at).toLocaleString("en-GB")} by {report.created_by} from records as of {fmtDate(report.as_of)} · {report.calc_version} · inputs SHA-256 {report.inputs_sha256.slice(0, 12)}…
        </p>
        <p className="muted small">{r.authorization}</p>
      </section>
    </>
  );
}

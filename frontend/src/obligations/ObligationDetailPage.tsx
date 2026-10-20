import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, todayIso } from "../api";
import { useSession } from "../auth/session";
import { VoidButton } from "../cash/AccountPage";
import { formatMoney, parseMajorToMinor } from "../money";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { exponentOf, Field } from "../settings/fields";
import { CATEGORY_LABELS, OB_STATUS, type Allocation, type Obligation } from "./types";

type Detail = Obligation & { allocations: Allocation[] };
type LedgerTxn = { id: string; business_date: string; amount_minor: number; currency: string; description: string; account_label: string };

export function ObligationDetailPage() {
  const { obligationId } = useParams();
  const { active, options } = useSession();
  const [ob, setOb] = useState<Detail | null>(null);
  const [ledger, setLedger] = useState<LedgerTxn[]>([]);
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);
  const root = active ? `/api/businesses/${active.business_id}` : "";
  const base = `${root}/obligations/${obligationId}`;
  const canWrite = active?.allowed_actions.includes("obligations.write") ?? false;

  const load = useCallback(async () => {
    if (!root) return;
    const [o, l] = await Promise.all([api<Detail>(base), api<{ transactions: LedgerTxn[] }>(`${root}/ledger?limit=200`)]);
    if (o.ok) setOb(o.data); else setMessage({ kind: "danger", text: o.message });
    if (l.ok) setLedger(l.data.transactions);
  }, [base, root]);
  useEffect(() => { load(); }, [load]);

  if (!ob) return message ? <p className="badge badge-danger">{message.text}</p> : <p className="muted">Loading…</p>;
  const exp = exponentOf(options, ob.currency);
  const money = (m: number) => formatMoney(m, ob.currency, exp);
  const act = async (path: string, body: object, ok: string) => {
    const r = await api<Detail>(`${base}${path}`, "POST", body);
    if (r.ok) { setOb(r.data); setMessage({ kind: "success", text: ok }); return null; }
    setMessage({ kind: "danger", text: r.message });
    return r.message;
  };
  const isOpen = ob.status === "open" || ob.status === "partially_settled";
  const eligible = ledger.filter((t) => t.currency === ob.currency && (ob.direction === "receivable" ? t.amount_minor > 0 : t.amount_minor < 0));

  return (
    <>
      <div className="page-header">
        <div>
          <p className="muted small"><Link className="link" to="/records/obligations">Invoices &amp; bills</Link> / {ob.direction === "receivable" ? "Customer invoice" : "Supplier bill"}</p>
          <h1 className="page-title">{ob.document_number} · {ob.party.name}</h1>
          <p className="page-sub badges">
            <span className={`badge ${OB_STATUS[ob.status][0]}`}>{OB_STATUS[ob.status][1]}</span>
            {ob.overdue && <span className="badge badge-danger">{ob.days_overdue} days overdue</span>}
            {ob.disputed && <span className="badge badge-warning">Disputed: {ob.dispute_reason}</span>}
            {ob.cancelled && <span className="badge badge-muted">Cancelled: {ob.cancel_reason}</span>}
          </p>
        </div>
      </div>
      {message && <p className={`badge badge-${message.kind}`} role="status">{message.text}</p>}
      {ob.direction === "receivable" && <p><Link className="link" to={`/receivables-guardian?invoice=${ob.id}`}>Review this invoice in Receivables Guardian</Link></p>}

      <div className="kpi-grid">
        <section className="card kpi"><span className="kpi-label">Original amount</span><span className="kpi-value num">{money(ob.amount_minor)}</span><span className="muted small">Never changed</span></section>
        <section className="card kpi"><span className="kpi-label">Paid (net of reversals)</span><span className="kpi-value num">{money(ob.allocated_minor)}</span><span className="muted small">{ob.allocations.length} entries</span></section>
        <section className="card kpi kpi-strong"><span className="kpi-label">Outstanding</span><span className="kpi-value num">{money(ob.outstanding_minor)}</span><span className="muted small">Due {fmtDate(ob.due_date)}</span></section>
        <section className="card kpi"><span className="kpi-label">Details</span><span className="small">{CATEGORY_LABELS[ob.category]} · issued {fmtDate(ob.issue_date)}</span>
          <span className="muted small">Source: {ob.source_reference}{ob.settlement_account ? ` · expected in ${ob.settlement_account.label}` : ""}</span></section>
      </div>

      <section className="card" aria-labelledby="alloc-title">
        <h2 id="alloc-title" className="card-title">Payments and reversals</h2>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Date</th><th>Type</th><th>Bank transaction</th><th>Note</th><th className="right">Amount</th>{canWrite && <th />}</tr></thead>
            <tbody>
              {ob.allocations.length === 0 && <tr><td colSpan={6} className="muted">No payments yet.</td></tr>}
              {ob.allocations.map((a) => (
                <tr key={a.id}>
                  <td className="nowrap">{fmtDate(a.allocation_date)}</td>
                  <td>{a.kind === "payment" ? (a.reversed ? <span className="badge badge-muted">Payment (reversed)</span> : "Payment") : <span className="badge badge-warning">Reversal</span>}</td>
                  <td className="wrap">{a.ledger_txn ? `${fmtDate(a.ledger_txn.business_date)} · ${a.ledger_txn.description}` : <span className="muted">Not linked</span>}</td>
                  <td className="wrap">{a.note ?? ""} <span className="muted small">by {a.created_by}</span></td>
                  <td className={`num right ${a.amount_minor < 0 ? "neg" : ""}`}>{money(a.amount_minor)}</td>
                  {canWrite && <td>{a.kind === "payment" && !a.reversed && (
                    <VoidButton label="Reverse" onVoid={(reason) => act(`/allocations/${a.id}/reverse`, { reason, reversal_date: todayIso() }, "Payment reversed.")} />
                  )}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canWrite && isOpen && <PaymentForm ob={ob} exp={exp} eligible={eligible} onPay={(body) => act("/allocations", body, "Payment recorded.")} />}
      </section>

      {ob.direction === "payable" && isOpen && (ob.category === "marketing" || ob.category === "other_expense") && (
        <DeferralPermissions base={base} ob={ob} canWrite={canWrite} />
      )}

      {canWrite && !ob.cancelled && (
        <section className="card actions-card" aria-labelledby="manage-title">
          <h2 id="manage-title" className="card-title">Manage</h2>
          <div className="badges">
            {ob.disputed
              ? <button type="button" className="btn btn-ghost" onClick={() => act("/dispute", { disputed: false }, "Dispute cleared.")}>Clear dispute</button>
              : <VoidButton label="Mark as disputed" onVoid={(reason) => act("/dispute", { disputed: true, reason }, "Marked as disputed.")} />}
            {ob.allocated_minor === 0 && <VoidButton label="Cancel invoice or bill" onVoid={(reason) => act("/cancel", { reason }, "Cancelled.")} />}
          </div>
        </section>
      )}
    </>
  );
}

function PaymentForm({ ob, exp, eligible, onPay }: { ob: Detail; exp: number; eligible: LedgerTxn[]; onPay: (b: object) => Promise<string | null> }) {
  const [f, setF] = useState({ amount: "", allocation_date: todayIso(), ledger_txn_id: "", note: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  // An earlier error (e.g. "more than outstanding") is stale once a payment or reversal changes the balance.
  useEffect(() => setErrors({}), [ob.outstanding_minor]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(f.amount, exp, 1e15);
    if (!parsed.ok) return setErrors({ amount_minor: parsed.error });
    if (parsed.minor > ob.outstanding_minor) return setErrors({ amount_minor: "More than the outstanding amount." });
    const body: Record<string, unknown> = { amount_minor: parsed.minor, currency: ob.currency, allocation_date: f.allocation_date };
    if (f.ledger_txn_id) body.ledger_txn_id = f.ledger_txn_id;
    if (f.note.trim()) body.note = f.note;
    const err = await onPay(body);
    setErrors(err ? { _form: err } : {});
    if (!err) setF({ ...f, amount: "", note: "", ledger_txn_id: "" });
  };
  return (
    <form className="stack-form" onSubmit={submit} noValidate aria-label="Record payment">
      <div className="form-grid form-grid-4">
        <Field label={`Amount (${ob.currency})`} error={errorFor(errors, "amount_minor", "currency")}>
          <input inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
        </Field>
        <Field label="Bank transaction (optional)" error={errorFor(errors, "ledger_txn_id")}>
          <select className="select" value={f.ledger_txn_id} onChange={(e) => {
            const t = eligible.find((x) => x.id === e.target.value);
            setF({ ...f, ledger_txn_id: e.target.value, allocation_date: t ? t.business_date : f.allocation_date });
          }}>
            <option value="">Not linked</option>
            {eligible.map((t) => <option key={t.id} value={t.id}>{t.business_date} · {t.account_label} · {t.description}</option>)}
          </select>
        </Field>
        <Field label="Date" error={errorFor(errors, "allocation_date")}>
          <input type="date" value={f.allocation_date} max={todayIso()} disabled={!!f.ledger_txn_id}
                 onChange={(e) => setF({ ...f, allocation_date: e.target.value })} />
        </Field>
        <Field label="Note (optional)" error={errorFor(errors, "note")}>
          <input value={f.note} maxLength={200} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </Field>
      </div>
      {errors._form && <p className="badge badge-danger">{errors._form}</p>}
      <div className="form-actions"><button type="submit" className="btn btn-primary">Record payment</button></div>
    </form>
  );
}

type Permission = { id: string; agreed_due_date: string; evidence_reference: string; note: string | null; created_at: string; created_by: string; voided: boolean; void_reason: string | null };

// A documented agreement to pay this discretionary bill later. It changes nothing on the bill: a Rescue Plan option may only
// "move" a bill that has one.
function DeferralPermissions({ base, canWrite }: { base: string; ob: Detail; canWrite: boolean }) {
  const [rows, setRows] = useState<Permission[]>([]);
  const [date, setDate] = useState("");
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { const r = await api<Permission[]>(`${base}/deferral-permissions`); if (r.ok) setRows(r.data); }, [base]);
  useEffect(() => { load(); }, [load]);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!date || !ref.trim()) return setError("Enter the agreed date and where the agreement is documented.");
    const r = await api<Permission>(`${base}/deferral-permissions`, "POST", { agreed_due_date: date, evidence_reference: ref });
    if (!r.ok) return setError(r.message);
    setError(null); setDate(""); setRef(""); load();
  };
  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="perm-title">
      <h2 id="perm-title" className="card-title">Permission to pay later</h2>
      {rows.length === 0 && <p className="muted">No permission is documented.</p>}
      {rows.map((p) => (
        <p key={p.id} className={p.voided ? "muted" : ""}>
          Pay by <strong>{fmtDate(p.agreed_due_date)}</strong> · {p.evidence_reference} <span className="muted small">· recorded by {p.created_by}</span>
          {p.voided && <span className="badge badge-muted"> Voided: {p.void_reason}</span>}
          {canWrite && !p.voided && <> <VoidButton onVoid={async (reason) => { const r = await api(`${base}/deferral-permissions/${p.id}/void`, "POST", { reason }); if (r.ok) { load(); return null; } return r.message; }} /></>}
        </p>
      ))}
      {canWrite && (
        <form className="form-grid" onSubmit={add} noValidate aria-label="Document a permission">
          {error && <p className="badge badge-danger span-2" role="alert">{error}</p>}
          <Field label="Supplier agreed to be paid by"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field>
          <Field label="Where it is documented"><input value={ref} maxLength={120} onChange={(e) => setRef(e.target.value)} /></Field>
          <div className="form-actions span-2"><button type="submit" className="btn btn-primary">Document permission</button></div>
        </form>
      )}
    </section>
  );
}

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmtDate, todayIso } from "../api";
import { useSession } from "../auth/session";
import { formatMoney, parseMajorToMinor } from "../money";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { exponentOf, Field, KIND_LABELS } from "../settings/fields";
import { ACCOUNT_STATUS, ACCOUNT_STATUS_HELP, PERIOD_STATUS, type AccountPosition, type Position } from "./types";

export function AccountPage() {
  const { accountId } = useParams();
  const { active, options } = useSession();
  const [pos, setPos] = useState<Position | null>(null);
  const [error, setError] = useState<string | null>(null);
  const base = active ? `/api/businesses/${active.business_id}` : "";
  const canManage = active?.allowed_actions.includes("cash.manage") ?? false;

  const load = useCallback(async () => {
    if (!base) return;
    const r = await api<Position>(`${base}/cash/position`);
    if (r.ok) setPos(r.data); else setError(r.message);
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const acc = pos?.accounts.find((a) => a.account.id === accountId);
  if (error) return <p className="badge badge-danger">{error}</p>;
  if (!pos) return <p className="muted">Loading…</p>;
  if (!acc) return <section className="card">Account not found. <Link className="link" to="/records">Back to Records</Link></section>;

  const exp = exponentOf(options, acc.account.currency);
  const money = (m: number | null) => (m === null ? "Unknown" : formatMoney(m, acc.account.currency, exp));
  const [cls, label] = ACCOUNT_STATUS[acc.status];
  const voidItem = async (kind: string, id: string, reason: string) => {
    const r = await api<Position>(`${base}/cash/${kind}/${id}/void`, "POST", { reason });
    if (r.ok) { setPos(r.data); return null; }
    return r.message;
  };

  return (
    <>
      <div className="page-header">
        <div>
          <p className="muted small"><Link className="link" to="/records">Records</Link> / Account</p>
          <h1 className="page-title">{acc.account.label}</h1>
          <p className="page-sub">{KIND_LABELS[acc.account.kind]} · {acc.account.currency} · as of {fmtDate(pos.as_of)}
            <span className={`badge ${cls} inline-badge`}>{label}</span></p>
        </div>
      </div>

      <div className="kpi-grid">
        <Kpi label="Verified balance" value={money(acc.verified_balance_minor)}
             note={acc.evidence_through ? `Verified through ${fmtDate(acc.evidence_through)}` : "No balance entered"} />
        <Kpi label="Restricted" value={money(acc.restricted_minor)} note={`${acc.restrictions.length} active restriction(s)`} />
        <Kpi label="Usable" value={money(acc.usable_minor)} note="Verified balance minus restricted funds" strong />
        <Kpi label="Uncovered movements" value={money(acc.uncovered_movements.sum_minor)}
             note={`${acc.uncovered_movements.count} posted after the verified date, not included`} />
      </div>

      <section className={`account-status-note ${acc.status === "stale" || acc.status === "missing" ? "is-attention" : ""}`} aria-labelledby="account-status-title">
        <div>
          <h2 id="account-status-title">What this means</h2>
          <p>{ACCOUNT_STATUS_HELP[acc.status]}{acc.evidence_through ? ` Last checked ${fmtDate(acc.evidence_through)}.` : ""}</p>
        </div>
        {(acc.status === "stale" || acc.status === "missing") && canManage && <a className="btn btn-primary" href="#balance-anchor-form">Update balance evidence</a>}
        {(acc.status === "stale" || acc.status === "missing") && !canManage && <span className="muted small">Ask an owner or bookkeeper to update the balance evidence.</span>}
      </section>

      {acc.issues.length > 0 && (
        <section className="card" aria-labelledby="issues-title">
          <h2 id="issues-title" className="card-title">What needs attention</h2>
          <ul className="issue-list">{acc.issues.map((i, n) => <li key={n}><span className="badge badge-warning">{i.code.replace(/_/g, " ")}</span> {i.message}</li>)}</ul>
        </section>
      )}

      <section className="card" aria-labelledby="periods-title">
        <h2 id="periods-title" className="card-title">Reconciliation periods</h2>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Period</th><th className="right">Opening</th><th className="right">Movements</th><th className="right">Closing</th><th className="right">Residual</th><th>Coverage gaps</th><th>Status</th></tr></thead>
            <tbody>
              {acc.periods.length === 0 && <tr><td colSpan={7} className="muted">Enter at least two balances to reconcile a period.</td></tr>}
              {acc.periods.map((p) => (
                <tr key={p.to_anchor_id}>
                  <td className="nowrap">{fmtDate(p.from_date)} → {fmtDate(p.to_date)}</td>
                  <td className="num right">{money(p.opening_minor)}</td>
                  <td className="num right">{money(p.movements_minor)} <span className="muted small">({p.movement_count})</span></td>
                  <td className="num right">{money(p.closing_minor)}</td>
                  <td className={`num right ${p.residual_minor ? "neg" : ""}`}>{money(p.residual_minor)}</td>
                  <td>{p.coverage_gaps.length ? p.coverage_gaps.map((g) => `${fmtDate(g.start)}–${fmtDate(g.end)}`).join(", ") : "None"}</td>
                  <td><span className={`badge ${PERIOD_STATUS[p.status][0]}`}>{PERIOD_STATUS[p.status][1]}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="split split-even">
        <AnchorsCard acc={acc} base={base} canManage={canManage} money={money} exp={exp} onChange={setPos} onVoid={voidItem} />
        <CoverageCard acc={acc} base={base} canManage={canManage} onChange={setPos} />
      </div>
      <RestrictionsCard acc={acc} base={base} canManage={canManage} money={money} exp={exp} onChange={setPos} onVoid={voidItem} />
    </>
  );
}

function Kpi({ label, value, note, strong }: { label: string; value: string; note: string; strong?: boolean }) {
  return (
    <section className={`card kpi ${strong ? "kpi-strong" : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value num">{value}</span>
      <span className="muted small">{note}</span>
    </section>
  );
}

export function VoidButton({ label = "Void", onVoid }: { label?: string; onVoid: (reason: string) => Promise<string | null> }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (!open) return <button type="button" className="btn btn-ghost btn-small" onClick={() => setOpen(true)}>{label}</button>;
  return (
    <span className="void-form">
      <input aria-label="Reason" placeholder="Reason" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />
      <button type="button" className="btn btn-primary btn-small" onClick={async () => {
        if (!reason.trim()) return setError("Give a reason.");
        setError(await onVoid(reason));
      }}>Confirm</button>
      <button type="button" className="btn btn-ghost btn-small" onClick={() => { setOpen(false); setError(null); }}>Cancel</button>
      {error && <span className="field-error block">{error}</span>}
    </span>
  );
}

type CardProps = {
  acc: AccountPosition; base: string; canManage: boolean; onChange: (p: Position) => void;
  money?: (m: number | null) => string; exp?: number; onVoid?: (kind: string, id: string, reason: string) => Promise<string | null>;
};

function AnchorsCard({ acc, base, canManage, money, exp, onChange, onVoid }: CardProps) {
  const [form, setForm] = useState({ anchor_date: todayIso(), amount: "", source_kind: acc.account.kind === "cash" ? "cash_count" : acc.account.kind === "mobile_wallet" ? "wallet_statement" : "bank_statement", source_reference: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(form.amount, exp!, 1e15, true);
    if (!parsed.ok) return setErrors({ balance_minor: parsed.error });
    const r = await api<Position>(`${base}/cash/anchors`, "POST", {
      account_id: acc.account.id, anchor_date: form.anchor_date, balance_minor: parsed.minor, currency: acc.account.currency,
      source_kind: form.source_kind, source_reference: form.source_reference });
    if (!r.ok) return setErrors(r.errors);
    setErrors({}); setForm({ ...form, amount: "", source_reference: "" }); onChange(r.data);
  };
  return (
    <section className="card" aria-labelledby="anchors-title">
      <h2 id="anchors-title" className="card-title">Balance anchors</h2>
      <p className="muted small">A balance anchor is the end-of-day balance shown on a bank or wallet statement, or counted cash. Two anchors let the app compare the recorded transactions with the balance change.</p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Date</th><th className="right">Balance</th><th>Source</th>{canManage && <th />}</tr></thead>
          <tbody>
            {acc.anchors.length === 0 && <tr><td colSpan={4} className="muted">No balances entered.</td></tr>}
            {[...acc.anchors].reverse().map((a) => (
              <tr key={a.id}>
                <td className="nowrap">{fmtDate(a.anchor_date)}</td>
                <td className="num right">{money!(a.balance_minor)}</td>
                <td className="wrap">{a.source_kind.replace(/_/g, " ")} · {a.source_reference}</td>
                {canManage && <td><VoidButton onVoid={(r) => onVoid!("anchors", a.id, r)} /></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form id="balance-anchor-form" className="stack-form" onSubmit={submit} noValidate aria-label="Add balance">
          {errors._form && <p className="badge badge-danger">{errors._form}</p>}
          <div className="form-grid">
            <Field label="Date (end of day)" error={errorFor(errors, "anchor_date")}>
              <input type="date" value={form.anchor_date} max={todayIso()} onChange={(e) => setForm({ ...form, anchor_date: e.target.value })} />
            </Field>
            <Field label={`Balance (${acc.account.currency})`} error={errorFor(errors, "balance_minor", "currency")} hint="Use a minus sign for an overdraft.">
              <input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </Field>
            <Field label="Source" error={errorFor(errors, "source_kind")}>
              <select className="select" value={form.source_kind} onChange={(e) => setForm({ ...form, source_kind: e.target.value })}>
                <option value="bank_statement">Bank statement</option>
                <option value="wallet_statement">Wallet statement</option>
                <option value="cash_count">Cash count</option>
                <option value="opening_balance">Opening balance</option>
              </select>
            </Field>
            <Field label="Statement reference" error={errorFor(errors, "source_reference")}>
              <input value={form.source_reference} maxLength={120} placeholder="e.g. Oct statement p.2" onChange={(e) => setForm({ ...form, source_reference: e.target.value })} />
            </Field>
          </div>
          <div className="form-actions"><button type="submit" className="btn btn-primary">Add balance</button></div>
        </form>
      )}
    </section>
  );
}

type ImportRow = { id: string; source_name: string; status: string; account_label: string };

function CoverageCard({ acc, base, canManage, onChange }: CardProps) {
  const [imports, setImports] = useState<ImportRow[]>([]);
  const [form, setForm] = useState({ period_start: "", period_end: "", source_kind: "statement", import_batch_id: "", note: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  useEffect(() => {
    api<ImportRow[]>(`${base}/imports`).then((r) => r.ok && setImports(r.data.filter((i) => i.status === "posted" && i.account_label === acc.account.label)));
  }, [base, acc.account.label]);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body: Record<string, unknown> = { account_id: acc.account.id, period_start: form.period_start, period_end: form.period_end, source_kind: form.source_kind };
    if (form.source_kind === "import_batch") body.import_batch_id = form.import_batch_id;
    if (form.note.trim()) body.note = form.note;
    if (!form.period_start || !form.period_end) return setErrors({ period_start: "Choose both dates." });
    const r = await api<Position>(`${base}/cash/coverage`, "POST", body);
    if (!r.ok) return setErrors(r.errors);
    setErrors({}); setForm({ ...form, note: "" }); onChange(r.data);
  };
  return (
    <section className="card" aria-labelledby="coverage-title">
      <h2 id="coverage-title" className="card-title">Source coverage</h2>
      <p className="muted small">Confirm that you checked the statement for this date range. Coverage helps identify missing records; it does not add transactions or make an incorrect balance reconcile.</p>
      <ul className="chip-list">
        {acc.coverage.length === 0 && <li className="muted">No coverage declared.</li>}
        {acc.coverage.map((c) => <li key={c.start} className="badge badge-info">{fmtDate(c.start)} – {fmtDate(c.end)}</li>)}
      </ul>
      {canManage && (
        <form className="stack-form" onSubmit={submit} noValidate aria-label="Declare coverage">
          {errors._form && <p className="badge badge-danger">{errors._form}</p>}
          <div className="form-grid">
            <Field label="From" error={errorFor(errors, "period_start")}>
              <input type="date" value={form.period_start} max={todayIso()} onChange={(e) => setForm({ ...form, period_start: e.target.value })} />
            </Field>
            <Field label="To" error={errorFor(errors, "period_end")}>
              <input type="date" value={form.period_end} max={todayIso()} onChange={(e) => setForm({ ...form, period_end: e.target.value })} />
            </Field>
            <Field label="Evidence" error={errorFor(errors, "source_kind", "import_batch_id")}>
              <select className="select" value={form.source_kind} onChange={(e) => setForm({ ...form, source_kind: e.target.value })}>
                <option value="statement">Statement checked</option>
                <option value="import_batch" disabled={imports.length === 0}>Posted import{imports.length ? "" : " (none)"}</option>
                <option value="manual_confirmation">Manual confirmation</option>
              </select>
            </Field>
            {form.source_kind === "import_batch" ? (
              <Field label="Import">
                <select className="select" value={form.import_batch_id} onChange={(e) => setForm({ ...form, import_batch_id: e.target.value })}>
                  <option value="">Choose an import</option>
                  {imports.map((i) => <option key={i.id} value={i.id}>{i.source_name}</option>)}
                </select>
              </Field>
            ) : (
              <Field label="Note (optional)" error={errorFor(errors, "note")}>
                <input value={form.note} maxLength={200} onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </Field>
            )}
          </div>
          <div className="form-actions"><button type="submit" className="btn btn-primary">Declare coverage</button></div>
        </form>
      )}
    </section>
  );
}

function RestrictionsCard({ acc, base, canManage, money, exp, onChange, onVoid }: CardProps) {
  const [form, setForm] = useState({ amount: "", reason: "", effective_from: todayIso(), effective_to: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(form.amount, exp!, 1e15);
    if (!parsed.ok) return setErrors({ amount_minor: parsed.error });
    const body: Record<string, unknown> = { account_id: acc.account.id, amount_minor: parsed.minor, currency: acc.account.currency,
                                            reason: form.reason, effective_from: form.effective_from };
    if (form.effective_to) body.effective_to = form.effective_to;
    const r = await api<Position>(`${base}/cash/restrictions`, "POST", body);
    if (!r.ok) return setErrors(r.errors);
    setErrors({}); setForm({ ...form, amount: "", reason: "" }); onChange(r.data);
  };
  return (
    <section className="card" aria-labelledby="restrict-title">
      <h2 id="restrict-title" className="card-title">Restricted funds</h2>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Reason</th><th>Effective</th><th className="right">Amount</th>{canManage && <th />}</tr></thead>
          <tbody>
            {acc.restrictions.length === 0 && <tr><td colSpan={4} className="muted">No active restrictions.</td></tr>}
            {acc.restrictions.map((r) => (
              <tr key={r.id}>
                <td className="wrap">{r.reason}</td>
                <td className="nowrap">{fmtDate(r.effective_from)} – {r.effective_to ? fmtDate(r.effective_to) : "open"}</td>
                <td className="num right">{money!(r.amount_minor)}</td>
                {canManage && <td><VoidButton label="Release" onVoid={(reason) => onVoid!("restrictions", r.id, reason)} /></td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {canManage && (
        <form className="stack-form" onSubmit={submit} noValidate aria-label="Add restriction">
          {errors._form && <p className="badge badge-danger">{errors._form}</p>}
          <div className="form-grid form-grid-4">
            <Field label={`Amount (${acc.account.currency})`} error={errorFor(errors, "amount_minor", "currency")}>
              <input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </Field>
            <Field label="Reason" error={errorFor(errors, "reason")}>
              <input value={form.reason} maxLength={200} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
            </Field>
            <Field label="From" error={errorFor(errors, "effective_from")}>
              <input type="date" value={form.effective_from} onChange={(e) => setForm({ ...form, effective_from: e.target.value })} />
            </Field>
            <Field label="Until (optional)" error={errorFor(errors, "effective_to")}>
              <input type="date" value={form.effective_to} onChange={(e) => setForm({ ...form, effective_to: e.target.value })} />
            </Field>
          </div>
          <div className="form-actions"><button type="submit" className="btn btn-primary">Add restriction</button></div>
        </form>
      )}
    </section>
  );
}

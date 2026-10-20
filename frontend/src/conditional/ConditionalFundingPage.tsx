import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney, parseMajorToMinor } from "../money";
import type { Obligation } from "../obligations/types";
import { exponentOf, Field } from "../settings/fields";

type MatchRun = { id: string; created_at: string; request: { amount_minor: number; currency: string }; eligible: number; excluded: number };
type MatchDetail = { id: string; request: { amount_minor: number; currency: string }; results: { currency: string; eligible: { id: string; name: string; provider: string; terms: { net_proceeds_minor: number; total_cost_minor: number; total_repayment_minor: number; schedule: { date: string; amount_minor: number; kind: string }[] } }[] } };
type Summary = { lowest: { date: string; amount_minor: number }; first_breach: { date: string; gap_minor: number } | null };
type Forecast = { expected?: Summary; cautious?: Summary; reserve_minor?: number | null };
type Snapshot = { id: string; generation: number; branch: string; as_of: string; inputs_sha256: string; results: { currency: string; forecast: Forecast; note: string; assumed_disbursement_date?: string; terms?: { net_proceeds_minor: number; total_cost_minor: number; total_repayment_minor: number; schedule: { date: string; amount_minor: number; kind: string }[] } } };
type Plan = { id: string; name: string; state: string; backup_decision: string | null; receipt: { obligation_id: string; document_number: string; customer: string; expected_amount_minor: number; currency: string; trigger_date: string };
  backup: { funding_match_run_id: string; product_id: string; name: string; provider: string; amount_minor: number; currency: string; label: string };
  snapshots: Snapshot[]; events: { type: string; at: string; actor: string; details: Record<string, unknown> }[]; notice: string };

const STATUS: Record<string, string> = { awaiting_receipt: "Awaiting receipt", receipt_recalculated: "Receipt verified · cash recalculated", backup_review_pending: "Trigger missed · backup review needed", backup_reviewed: "Backup reviewed" };
const BRANCH: Record<string, string> = { receipt_expected: "Expected receipt · no borrowing", backup_if_missed: "Backup funding if receipt is missed", missed_no_borrow: "Missed receipt · no borrowing", missed_with_backup: "Missed receipt · illustrative backup", settled_actual: "Verified receipt · current outlook" };

export function ConditionalFundingPage() {
  const { active, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}` : "";
  const plansApi = `${base}/conditional-funding-plans`;
  const canRead = active?.allowed_actions.includes("conditional.read") ?? false;
  const canWrite = active?.allowed_actions.includes("conditional.write") ?? false;
  const canEvaluate = active?.allowed_actions.includes("conditional.evaluate") ?? false;
  const canReview = active?.allowed_actions.includes("conditional.review") ?? false;
  const [plans, setPlans] = useState<Plan[]>([]);
  const [invoices, setInvoices] = useState<Obligation[]>([]);
  const [matches, setMatches] = useState<MatchRun[]>([]);
  const [run, setRun] = useState<MatchDetail | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [invoiceId, setInvoiceId] = useState("");
  const [amount, setAmount] = useState("");
  const [trigger, setTrigger] = useState(() => { const d = new Date(); d.setDate(d.getDate() + 7); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });
  const [runId, setRunId] = useState("");
  const [productId, setProductId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const money = (minor: number, currency: string) => formatMoney(minor, currency, exponentOf(options, currency));
  const invoiceOptions = useMemo(() => invoices.filter((i) => ["open", "partially_settled"].includes(i.status) && !i.disputed), [invoices]);
  const activeInvoice = invoiceOptions.find((i) => i.id === invoiceId);
  const eligibleProducts = run?.results.eligible ?? [];
  const selectedPlan = plans.find((p) => p.id === selected);

  const load = useCallback(async () => {
    if (!base || !canRead) return;
    const [p, i, m] = await Promise.all([
      api<Plan[]>(plansApi), api<{ items: Obligation[] }>(`${base}/obligations?direction=receivable`), api<MatchRun[]>(`${base}/funding-matches`),
    ]);
    if (!p.ok) setError(p.message); else { setPlans(p.data); if (!selected && p.data[0]) setSelected(p.data[0].id); }
    if (i.ok) { const available = i.data.items.filter((x) => ["open", "partially_settled"].includes(x.status) && !x.disputed); setInvoices(i.data.items); if (!invoiceId && available[0]) setInvoiceId(available[0].id); }
    if (m.ok) { setMatches(m.data); if (!runId && m.data[0]) setRunId(m.data[0].id); }
  }, [base, canRead, plansApi, selected, invoiceId, runId]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!runId) { setRun(null); return; }
    api<MatchDetail>(`${base}/funding-matches/${runId}`).then((r) => { if (r.ok) { setRun(r.data); setProductId(r.data.results.eligible[0]?.id ?? ""); } });
  }, [base, runId]);

  const act = async <T,>(url: string, body?: object): Promise<T | null> => {
    setBusy(true); setError(null); setNotice(null);
    const r = await api<T>(url, body ? "POST" : "GET", body);
    setBusy(false);
    if (!r.ok) { setError(r.message); return null; }
    return r.data;
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!activeInvoice || !run || !productId) return setError("Choose an open invoice and an eligible illustrative backup product first.");
    const parsed = parseMajorToMinor(amount, exponentOf(options, activeInvoice.currency), activeInvoice.outstanding_minor);
    if (!parsed.ok) return setError(parsed.error);
    setBusy(true); setError(null); setNotice(null);
    const r = await api<Plan>(plansApi, "POST", { name: name.trim() || `Receipt for ${activeInvoice.document_number}`, obligation_id: activeInvoice.id,
      expected_amount_minor: parsed.minor, trigger_date: trigger, funding_match_run_id: run.id, product_id: productId });
    setBusy(false);
    if (!r.ok) { setError(r.message); return; }
    setNotice("Plan created. The receipt and backup scenarios are saved for review."); setPlans((old) => [r.data, ...old]); setSelected(r.data.id);
  };
  const evaluate = async () => {
    if (!selectedPlan) return;
    const updated = await act<Plan>(`${plansApi}/${selectedPlan.id}/evaluate`, {});
    if (updated) { setPlans((old) => old.map((p) => p.id === updated.id ? updated : p)); setNotice(updated.state === "awaiting_receipt" ? "No trigger change yet; the plan remains waiting." : `Plan updated: ${STATUS[updated.state] ?? updated.state}.`); }
  };
  const review = async (decision: "proceed" | "decline") => {
    if (!selectedPlan) return;
    const updated = await act<Plan>(`${plansApi}/${selectedPlan.id}/review`, { decision });
    if (updated) { setPlans((old) => old.map((p) => p.id === updated.id ? updated : p)); setNotice(decision === "proceed" ? "Backup reviewed. Continue through Funding Matches, readiness, and the existing owner approval workflow if you decide to apply." : "Backup declined and recorded."); }
  };

  return <>
    <div className="page-header"><div><h1 className="page-title">Conditional Funding Plan</h1>
      <p className="page-sub">Plan around an expected customer receipt, then compare a no-borrow path with an illustrative backup if it is late.</p></div>
      {canRead && <button className="btn btn-ghost" type="button" onClick={load}>Refresh</button>}</div>
    <p className="muted small">{selectedPlan?.notice ?? "A forecast is an estimate. Only a posted transaction linked to an invoice payment confirms settlement. A missed date never submits an application."}</p>
    {error && <p className="badge badge-danger" role="alert">{error}</p>}{notice && <p className="badge badge-success" role="status">{notice}</p>}
    {!canRead && <section className="card"><p>Only owners and bookkeepers can view conditional funding plans.</p></section>}
    {canRead && <>
      {canWrite && <section className="card" aria-labelledby="new-plan"><h2 id="new-plan" className="card-title">Create a plan</h2>
        <p className="muted small">Choose an outstanding invoice and a previously saved eligible match. The match remains illustrative and is not an application.</p>
        {matches.length === 0 && <p>No saved funding match yet. <Link className="link" to="/funding-matches">Create a funding comparison</Link> first.</p>}
        {matches.length > 0 && <form className="form-grid" onSubmit={submit}>
          <Field label="Plan name"><input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} placeholder="October customer receipt" /></Field>
          <Field label="Customer invoice"><select value={invoiceId} onChange={(e) => { setInvoiceId(e.target.value); setAmount(""); }}>
            {invoiceOptions.map((i) => <option key={i.id} value={i.id}>{i.document_number} · {i.party.name} · {money(i.outstanding_minor, i.currency)} outstanding</option>)}</select></Field>
          {activeInvoice && <Field label={`Expected receipt (${activeInvoice.currency})`}><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={money(activeInvoice.outstanding_minor, activeInvoice.currency)} /></Field>}
          <Field label="Trigger date"><input type="date" value={trigger} onChange={(e) => setTrigger(e.target.value)} required /></Field>
          <Field label="Saved funding comparison"><select value={runId} onChange={(e) => setRunId(e.target.value)}>{matches.map((m) => <option key={m.id} value={m.id}>{m.request.currency} · {money(m.request.amount_minor, m.request.currency)} · {fmtDate(m.created_at)}</option>)}</select></Field>
          <Field label="Illustrative backup product"><select value={productId} onChange={(e) => setProductId(e.target.value)} disabled={!eligibleProducts.length}>
            {eligibleProducts.map((p) => <option key={p.id} value={p.id}>{p.name} · net {money(p.terms.net_proceeds_minor, run?.results.currency ?? "BDT")}</option>)}</select>
            {!eligibleProducts.length && <span className="muted small">That comparison has no eligible products. Select another saved comparison.</span>}</Field>
          <div className="form-actions span-2"><button type="submit" className="btn btn-primary" disabled={busy || !eligibleProducts.length || !activeInvoice}>Create conditional plan</button></div>
        </form>}
      </section>}
      <div className="funding-grid" style={{ marginTop: 16 }}>
        <section className="card"><h2 className="card-title">Plans</h2>
          {!plans.length ? <p className="muted">No conditional plans saved yet.</p> : <ul className="queue-list">{plans.map((p) => <li key={p.id} className="queue-item">
            <button className="link-button" type="button" onClick={() => setSelected(p.id)}><strong>{p.name}</strong></button><br />
            {p.receipt.document_number} · trigger {fmtDate(p.receipt.trigger_date)}<br /><span className="badge badge-info">{STATUS[p.state] ?? p.state}</span>
          </li>)}</ul>}
        </section>
        {selectedPlan && <section className="card"><h2 className="card-title">{selectedPlan.name}</h2>
          <p>{selectedPlan.receipt.customer} · <Link className="link" to={`/records/obligations/${selectedPlan.receipt.obligation_id}`}>{selectedPlan.receipt.document_number}</Link><br />
            Expected {money(selectedPlan.receipt.expected_amount_minor, selectedPlan.receipt.currency)} by {fmtDate(selectedPlan.receipt.trigger_date)}</p>
          <p>Backup: <strong>{selectedPlan.backup.name}</strong> · {selectedPlan.backup.label} · {money(selectedPlan.backup.amount_minor, selectedPlan.backup.currency)}</p>
          <span className="badge badge-info">{STATUS[selectedPlan.state] ?? selectedPlan.state}</span>
          {canEvaluate && selectedPlan.state === "awaiting_receipt" && <div className="form-actions" style={{ marginTop: 12 }}>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={evaluate}>Check receipt and trigger</button>
          </div>}
          {selectedPlan.state === "backup_review_pending" && <div className="alert-card alert-warning" style={{ marginTop: 12 }}>
            <strong>Trigger missed. Review the backup path.</strong><p>The app has recalculated both paths. No loan application was sent.</p>
            {canReview && <div className="form-actions"><button className="btn btn-primary" type="button" disabled={busy} onClick={() => review("proceed")}>Review and continue to funding</button>
              <button className="btn btn-ghost" type="button" disabled={busy} onClick={() => review("decline")}>Decline backup</button></div>}
          </div>}
          {selectedPlan.state === "backup_reviewed" && selectedPlan.backup_decision === "proceed" && <p className="muted">Continue in <Link className="link" to="/funding-matches">Funding Matches</Link> to review readiness, then use the existing approval and sandbox flow if you choose to proceed.</p>}
          <h3 className="card-title" style={{ marginTop: 18 }}>Cash paths</h3>
          <div className="table-wrap"><table className="table"><thead><tr><th>Scenario</th><th>Expected low</th><th>Cautious low</th><th>Reserve</th><th>Source</th></tr></thead><tbody>
            {selectedPlan.snapshots.map((s) => <tr key={s.id}><td>{BRANCH[s.branch] ?? s.branch}<br /><span className="muted small">As of {fmtDate(s.as_of)}</span></td>
              <td>{s.results.forecast.expected ? `${money(s.results.forecast.expected.lowest.amount_minor, s.results.currency)} · ${fmtDate(s.results.forecast.expected.lowest.date)}` : "No path"}</td>
              <td>{s.results.forecast.cautious ? `${money(s.results.forecast.cautious.lowest.amount_minor, s.results.currency)} · ${fmtDate(s.results.forecast.cautious.lowest.date)}` : "No path"}</td>
              <td>{s.results.forecast.reserve_minor === null || s.results.forecast.reserve_minor === undefined ? "Not set" : money(s.results.forecast.reserve_minor, s.results.currency)}</td>
              <td><span className="muted small">Inputs {s.inputs_sha256.slice(0, 12)}…</span>{s.results.assumed_disbursement_date && <><br />Funds assumed {fmtDate(s.results.assumed_disbursement_date)}</>}</td></tr>)}
          </tbody></table></div>
          <h3 className="card-title" style={{ marginTop: 18 }}>History</h3>
          <ul>{selectedPlan.events.map((e, i) => <li key={`${e.type}-${i}`}><strong>{e.type.replace(/_/g, " ")}</strong> · {e.actor} · {new Date(e.at).toLocaleString()}</li>)}</ul>
        </section>}
      </div>
    </>}
  </>;
}

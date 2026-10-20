import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { RegionNote } from "../region/RegionNote";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney, parseMajorToMinor } from "../money";
import type { FieldErrors } from "../settings/apiErrors";
import { exponentOf, Field } from "../settings/fields";
import { DocumentsCard } from "./DocumentsCard";
import { ReadinessCard } from "./ReadinessCard";
import { FundingPassportCard } from "./FundingPassportCard";

// Layout follows images/06-funding-matches.png (funding need, Compare options with Readiness beside it); styling follows design.md.
// The Funding Passport card of the mockup belongs to a later prompt and is not built here.
type Lowest = { date: string; amount_minor: number };
type Breach = { date: string; balance_minor: number; gap_minor: number } | null;
type PathView = { lowest: Lowest; first_breach: Breach };
type Forecast = { expected: PathView; cautious: PathView; reserve_minor: number | null };
type Terms = { principal_minor: number; processing_fee_minor: number; net_proceeds_minor: number; interest_or_fee_minor: number; total_repayment_minor: number;
               total_cost_minor: number; cost_bps_of_principal: number; disbursement_date: string; schedule: { date: string; amount_minor: number; kind: string }[] };
type Source = { label: string; updated: string; catalogue_version: string };
type Filter = { code: string; passed: boolean; message: string };
type Eligible = { id: string; name: string; provider: string; label: string; rank: number; badges: { code: string; label: string }[]; rank_reasons: string[]; repayment_rule: string;
                  terms: Terms; forecast: Forecast; source: Source; funding_days: number; filters: Filter[] };
type Excluded = { id: string; name: string; provider: string; label: string; reasons: string[]; source: Source };
type Run = {
  id: string; created_at: string; created_by: string; catalogue_version: string; inputs_sha256: string;
  results: { currency: string; label: string; notice: string; catalogue_updated: string; catalogue_source: string; baseline: Forecast; ranking_rule: string;
             need: { amount_minor: number; needed_by: string; purpose: string }; eligible: Eligible[]; excluded: Excluded[] };
};
type Row = { id: string; created_at: string; request: { amount_minor: number; currency: string; needed_by: string }; eligible: number; excluded: number };
type Catalogue = { purposes: string[]; documents: string[]; security_declared: string[] };

const BADGE_CLS: Record<string, string> = { best_repayment_fit: "badge-success", fastest_funding: "badge-info", lowest_cost: "badge-warning" };
const PURPOSE: Record<string, string> = { working_capital: "Working capital", inventory: "Inventory", invoice_gap: "Invoice gap", equipment: "Equipment", other: "Other" };
const DOC: Record<string, string> = { bank_statements: "Bank statements", id: "Owner ID", trade_licence: "Trade licence", invoices: "Invoices", wallet_history: "Wallet history", tax_certificate: "Tax certificate" };

export function FundingMatchesPage() {
  const { active, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}/funding-matches` : "";
  const canRead = active?.allowed_actions.includes("funding.read") ?? false;
  const canRun = active?.allowed_actions.includes("funding.run") ?? false;
  const readiness = { read: active?.allowed_actions.includes("readiness.read") ?? false, run: active?.allowed_actions.includes("readiness.run") ?? false };
  const docs = { read: active?.allowed_actions.includes("documents.read") ?? false, write: active?.allowed_actions.includes("documents.write") ?? false };
  const passport = { read: active?.allowed_actions.includes("passport.read") ?? false, write: active?.allowed_actions.includes("passport.write") ?? false };
  const [docsVersion, setDocsVersion] = useState(0);
  const bizBase = active ? `/api/businesses/${active.business_id}` : "";
  const [run, setRun] = useState<Run | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [loaded, setLoaded] = useState(false);
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));

  const load = useCallback(async () => {
    if (!base || !canRead) { setLoaded(true); return; }
    const [l, c] = await Promise.all([api<Row[]>(base), api<Catalogue>(`${base}/catalogue`)]);
    if (c.ok) setCat(c.data);
    if (l.ok) {
      setRows(l.data);
      if (l.data[0]) { const r = await api<Run>(`${base}/${l.data[0].id}`); if (r.ok) setRun(r.data); }
    }
    setLoaded(true);
  }, [base, canRead]);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Funding matches</h1>
          <p className="page-sub">Compare financing by eligibility, true cost, and repayment fit. Sample terms only: nothing is applied for or shared.</p>
        </div>
      </div>
      <RegionNote catalogue />
      {!canRead && <section className="card"><p className="muted">Only owners and bookkeepers can use Funding Matches.</p></section>}
      {canRead && canRun && cat && <NeedForm base={base} cat={cat} onSaved={(r) => { setRun(r); load(); }} />}
      {canRead && loaded && !run && <section className="card" style={{ marginTop: 16 }}><p className="muted">No funding comparison has been saved yet. Describe the funding need above.</p></section>}
      {run && <RunView run={run} money={money} readiness={readiness.read
        ? <ReadinessCard base={bizBase} runId={run.id} eligible={run.results.eligible} excluded={run.results.excluded} canRun={readiness.run} docsVersion={docsVersion} />
        : null} />}
      {docs.read && <div style={{ marginTop: 16 }}><DocumentsCard base={bizBase} canWrite={docs.write} onChanged={() => setDocsVersion((v) => v + 1)} /></div>}
      {passport.read && <FundingPassportCard base={bizBase} canWrite={passport.write} />}
      {rows.length > 1 && (
        <section className="card" style={{ marginTop: 16 }}>
          <h2 className="card-title">Saved comparisons</h2>
          <div className="table-wrap"><table className="table"><thead><tr><th>Saved</th><th>Need</th><th>By</th><th>Eligible</th><th /></tr></thead><tbody>
            {rows.map((r) => (
              <tr key={r.id}><td className="nowrap">{new Date(r.created_at).toLocaleString("en-GB")}</td><td className="nowrap">{money(r.request.amount_minor, r.request.currency)}</td>
                  <td className="nowrap">{fmtDate(r.request.needed_by)}</td><td>{r.eligible} of {r.eligible + r.excluded}</td>
                  <td><button type="button" className="link-button" onClick={async () => { const x = await api<Run>(`${base}/${r.id}`); if (x.ok) setRun(x.data); }}>Open</button></td></tr>
            ))}
          </tbody></table></div>
        </section>
      )}
    </>
  );
}

function NeedForm({ base, cat, onSaved }: { base: string; cat: Catalogue; onSaved: (r: Run) => void }) {
  const { profile, options } = useSession();
  const accounts = profile?.accounts ?? [];
  const [f, setF] = useState({ account: accounts[0]?.id ?? "", amount: "", by: "", purpose: "working_capital", months: "", security: [] as string[], docs: [] as string[] });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const currency = accounts.find((a) => a.id === f.account)?.currency ?? "BDT";
  const toggle = (k: "security" | "docs", v: string) => setF((s) => ({ ...s, [k]: s[k].includes(v) ? s[k].filter((x) => x !== v) : [...s[k], v] }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const amount = parseMajorToMinor(f.amount, exponentOf(options, currency), 1e15);
    if (!amount.ok) return setErrors({ amount_minor: amount.error });
    if (!f.by) return setErrors({ needed_by: "Choose the date the funds are needed." });
    if (!/^\d+$/.test(f.months)) return setErrors({ months_trading: "Enter whole months, for example 18." });
    setBusy(true);
    const r = await api<Run>(base, "POST", { amount_minor: amount.minor, currency, needed_by: f.by, purpose: f.purpose, receiving_account_id: f.account,
                                             months_trading: Number(f.months), security_available: f.security, documents_available: f.docs });
    setBusy(false);
    if (!r.ok) return setErrors({ _form: r.message, ...r.errors });
    setErrors({});
    onSaved(r.data);
  };
  return (
    <form className="card form-grid" onSubmit={submit} noValidate aria-label="Funding need" style={{ marginBottom: 16 }}>
      <h2 className="card-title span-2">Funding need</h2>
      {errors._form && <p className="badge badge-danger span-2" role="alert">{errors._form}</p>}
      <Field label={`Amount needed (${currency})`} error={errors.amount_minor}><input inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} placeholder="26,000.00" /></Field>
      <Field label="Needed by" error={errors.needed_by} hint="Within 13 weeks."><input type="date" value={f.by} onChange={(e) => setF({ ...f, by: e.target.value })} /></Field>
      <Field label="Receiving account" error={errors.receiving_account_id} hint="Must have a verified balance so the forecast can include the funds.">
        <select value={f.account} onChange={(e) => setF({ ...f, account: e.target.value })}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.label} · {a.currency}</option>)}</select>
      </Field>
      <Field label="Purpose" error={errors.purpose}>
        <select value={f.purpose} onChange={(e) => setF({ ...f, purpose: e.target.value })}>{cat.purposes.map((p) => <option key={p} value={p}>{PURPOSE[p] ?? p}</option>)}</select>
      </Field>
      <Field label="Months trading (your declaration)" error={errors.months_trading} hint="Not verified by this tool."><input inputMode="numeric" value={f.months} onChange={(e) => setF({ ...f, months: e.target.value })} /></Field>
      <fieldset className="span-2 adverse-set">
        <legend>What you can offer (declared, not verified)</legend>
        <div className="checks">
          {cat.security_declared.map((s) => <label key={s}><input type="checkbox" checked={f.security.includes(s)} onChange={() => toggle("security", s)} /> {s === "guarantor" ? "A guarantor" : "Collateral"}</label>)}
        </div>
        <p className="muted small">Documents you hold</p>
        <div className="checks">
          {cat.documents.map((d) => <label key={d}><input type="checkbox" checked={f.docs.includes(d)} onChange={() => toggle("docs", d)} /> {DOC[d] ?? d}</label>)}
        </div>
      </fieldset>
      <div className="form-actions span-2"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Comparing…" : "Compare options"}</button></div>
    </form>
  );
}

function RunView({ run, money, readiness }: { run: Run; money: (m: number, c: string) => string; readiness: ReactNode }) {
  const r = run.results;
  const cur = r.currency;
  const base = r.baseline.cautious;
  return (
    <>
      <section className="card review-head" aria-label="Funding need summary">
        <div>
          <p className="muted small" style={{ margin: 0 }}>Funding need</p>
          <h2 className="section-title">{money(r.need.amount_minor, cur)} by {fmtDate(r.need.needed_by)}</h2>
        </div>
        <div className="review-meta">
          <span>Purpose: {PURPOSE[r.need.purpose] ?? r.need.purpose}</span>
          <span>Without financing, lowest cash (cautious): {money(base.lowest.amount_minor, cur)} on {fmtDate(base.lowest.date)}</span>
        </div>
        <span className="badge badge-warning review-badge">{r.label}</span>
      </section>
      <p className="muted small" style={{ marginTop: 8 }}>{r.notice} Catalogue {run.catalogue_version}, terms updated {fmtDate(r.catalogue_updated)}. {r.catalogue_source}</p>

      <div className={readiness ? "funding-grid" : ""} style={{ marginTop: 16 }}>
      <section className="card" aria-labelledby="cmp-title">
        <h2 id="cmp-title" className="section-title">Compare options</h2>
        {r.eligible.length === 0 && <p className="muted">No product in this catalogue passed every eligibility filter. See the reasons below.</p>}
        {r.eligible.length > 0 && <p className="muted small">{r.ranking_rule}</p>}
        <ol className="queue-list">
          {r.eligible.map((p) => {
            const c = p.forecast.cautious;
            const breach = c.first_breach;
            return (
              <li key={p.id} className="queue-item">
                <div className="alert-head">
                  <span className="alert-title"><span className="queue-rank">{p.rank}</span> {p.name} <span className="muted small">· {p.provider}</span></span>
                  <span className="badges">{p.badges.map((b) => <span key={b.code} className={`badge ${BADGE_CLS[b.code]}`}>{b.label}</span>)}</span>
                </div>
                <dl className="alert-facts">
                  <div><dt>Total repayment</dt><dd className="nowrap">{money(p.terms.total_repayment_minor, cur)}</dd></div>
                  <div><dt>Total cost</dt><dd className="nowrap">{money(p.terms.total_cost_minor, cur)} ({p.terms.cost_bps_of_principal / 100}%)</dd></div>
                  <div><dt>Lowest cash, cautious path</dt><dd className={`nowrap ${breach ? "text-danger" : ""}`}>{money(c.lowest.amount_minor, cur)} on {fmtDate(c.lowest.date)}</dd></div>
                  <div><dt>Funds arrive (typical)</dt><dd className="nowrap">{fmtDate(p.terms.disbursement_date)}</dd></div>
                </dl>
                {breach
                  ? <p className="badge badge-warning">Still below the reserve from {fmtDate(breach.date)} on the cautious path.</p>
                  : <p className="badge badge-success">Stays above the reserve on the cautious path (within 13 weeks).</p>}
                <p className="muted small">Illustrative terms · approval not guaranteed · terms source: {p.source.label}, updated {fmtDate(p.source.updated)}</p>
                <details className="path-detail">
                  <summary>Why it ranks here, fees, and repayment dates</summary>
                  <ul>{p.rank_reasons.map((x) => <li key={x}>{x}</li>)}</ul>
                  <p className="small">Repayment rule: {p.repayment_rule}. Processing fee {money(p.terms.processing_fee_minor, cur)} is deducted, so you receive {money(p.terms.net_proceeds_minor, cur)}.
                    Only repayments inside the 13-week forecast change the lowest cash above.</p>
                  <div className="table-wrap"><table className="table"><thead><tr><th>Date</th><th>Payment</th><th className="right">Amount</th></tr></thead><tbody>
                    {p.terms.schedule.map((s, i) => <tr key={i}><td className="nowrap">{fmtDate(s.date)}</td><td>{s.kind === "installment" ? `Installment ${i + 1}` : "Repayment"}</td><td className="num">{money(s.amount_minor, cur)}</td></tr>)}
                  </tbody></table></div>
                  <p className="muted small">Expected path lowest: {money(p.forecast.expected.lowest.amount_minor, cur)} on {fmtDate(p.forecast.expected.lowest.date)}.</p>
                </details>
              </li>
            );
          })}
        </ol>
      </section>
      {readiness}
      </div>

      <section className="card" style={{ marginTop: 16 }} aria-labelledby="ex-title">
        <h2 id="ex-title" className="card-title">Not eligible</h2>
        {r.excluded.length === 0 && <p className="muted">Every product passed the filters.</p>}
        {r.excluded.map((p) => (
          <details key={p.id} className="path-detail">
            <summary><strong>{p.name}</strong> <span className="muted small">· {p.provider}</span> <span className="badge badge-muted">Excluded</span></summary>
            <ul>{p.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
            <p className="muted small">Terms source: {p.source.label}, updated {fmtDate(p.source.updated)} ({p.source.catalogue_version}).</p>
          </details>
        ))}
        <p className="muted small" style={{ marginTop: 12 }}>Saved {new Date(run.created_at).toLocaleString("en-GB")} by {run.created_by} · inputs SHA-256 {run.inputs_sha256.slice(0, 12)}…</p>
      </section>
    </>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api, fmtDate } from "../api";

// The Readiness card of images/06-funding-matches.png: one row per published condition with a status mark
// (+ present, ? uncertain, ! missing), always with the status word too so colour is never the only signal.
type Condition = { code: string; label: string; requirement: string; status: "present" | "missing" | "uncertain"; finding: string;
                   source: string; verification: string; improvement: string | null };
export type Check = {
  id: string; run_id: string; product_id: string; state: "ready" | "needs_verification" | "not_ready"; as_of: string; created_by: string; created_at: string;
  results: { product: { name: string; provider: string; source: { label: string; updated: string } }; headline: string; notice: string;
             counts: { present: number; missing: number; uncertain: number }; conditions: Condition[];
             improvements: { code: string; label: string; status: string; step: string }[]; window: { start: string; end: string } };
};
type Row = { id: string; product_id: string; created_at: string };
type Product = { id: string; name: string };

const MARK = { present: "+", uncertain: "?", missing: "!" } as const;
const WORD = { present: "Present", uncertain: "Uncertain", missing: "Missing" } as const;
const STATE = {
  ready: { cls: "badge-success", text: "Ready to apply" },
  needs_verification: { cls: "badge-warning", text: "Needs verification" },
  not_ready: { cls: "badge-danger", text: "Not ready" },
} as const;

export function ReadinessCard({ base, runId, eligible, excluded, canRun, docsVersion }: {
  base: string; runId: string; eligible: Product[]; excluded: Product[]; canRun: boolean; docsVersion: number;
}) {
  const url = `${base}/financing-readiness`;
  const [product, setProduct] = useState(eligible[0]?.id ?? excluded[0]?.id ?? "");
  const [rows, setRows] = useState<Row[]>([]);
  const [check, setCheck] = useState<Check | null>(null);
  const [checkedAtDocs, setCheckedAtDocs] = useState(docsVersion);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setProduct(eligible[0]?.id ?? excluded[0]?.id ?? ""); }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    const l = await api<Row[]>(`${url}?run_id=${runId}`);
    if (!l.ok) return setError(l.message);
    setRows(l.data);
  }, [url, runId]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const latest = rows.find((r) => r.product_id === product);
    if (!latest) { setCheck(null); return; }
    if (check?.id === latest.id) return;
    api<Check>(`${url}/${latest.id}`).then((r) => { if (r.ok) { setCheck(r.data); setCheckedAtDocs(docsVersion); } });
  }, [rows, product]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    setBusy(true);
    const r = await api<Check>(url, "POST", { run_id: runId, product_id: product });
    setBusy(false);
    if (!r.ok) return setError(r.message);
    setError(null);
    setCheck(r.data);
    setCheckedAtDocs(docsVersion);
    load();
  };

  const shown = check && check.product_id === product ? check : null;
  const s = shown ? STATE[shown.state] : null;
  return (
    <section className="card readiness-card" aria-labelledby="ready-title">
      <h2 id="ready-title" className="section-title">Readiness</h2>
      <label className="field readiness-pick">
        <span>Product</span>
        <select value={product} onChange={(e) => { setProduct(e.target.value); setError(null); }}>
          {eligible.length > 0 && <optgroup label="Potential matches">{eligible.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>}
          {excluded.length > 0 && <optgroup label="Not eligible">{excluded.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</optgroup>}
        </select>
      </label>
      {error && <p className="badge badge-danger block-badge" role="alert">{error}</p>}
      {!shown && <p className="muted">{canRun ? "Not checked yet for this product." : "Not checked yet for this product. An owner or bookkeeper can run the check."}</p>}
      {shown && s && (
        <>
          <div className="readiness-state">
            <span className={`badge ${s.cls}`}>{s.text}</span>
            <span className="muted small">{shown.results.counts.present} present · {shown.results.counts.missing} missing · {shown.results.counts.uncertain} uncertain</span>
          </div>
          <p className="readiness-headline">{shown.results.headline}</p>
          {docsVersion !== checkedAtDocs && <p className="notice small" role="status">Your document register changed after this check. Check again to include it.</p>}
          <ul className="ready-list">
            {shown.results.conditions.map((c) => (
              <li key={c.code} className={`ready-item is-${c.status}`}>
                <span className="ready-mark" aria-hidden="true">{MARK[c.status]}</span>
                <details className="ready-body">
                  <summary>
                    <span className="ready-label">{c.label}</span>
                    <span className="ready-word">{WORD[c.status]}</span>
                  </summary>
                  <p className="ready-finding">{c.finding}</p>
                  <dl className="ready-facts">
                    <div><dt>Requirement</dt><dd>{c.requirement}</dd></div>
                    <div><dt>Source</dt><dd>{c.source}</dd></div>
                    <div><dt>How to verify</dt><dd>{c.verification}</dd></div>
                  </dl>
                </details>
              </li>
            ))}
          </ul>
          {shown.results.improvements.length > 0 && (
            <>
              <h3 className="ready-subtitle">Improve before applying</h3>
              <ol className="ready-steps">
                {shown.results.improvements.map((i) => <li key={i.code}><strong>{i.label}:</strong> {i.step}</li>)}
              </ol>
            </>
          )}
          <p className="muted small">Checked {new Date(shown.created_at).toLocaleString("en-GB")} by {shown.created_by} on records as of {fmtDate(shown.as_of)}. {shown.results.notice}</p>
        </>
      )}
      {canRun && product && (
        <div className="form-actions">
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy}>{busy ? "Checking…" : shown ? "Check again" : "Check readiness"}</button>
        </div>
      )}
    </section>
  );
}

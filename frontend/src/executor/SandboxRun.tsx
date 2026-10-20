import { useEffect, useState } from "react";
import { api } from "../api";
import { CHECK_LABELS, type Check, type Proposal } from "../approvals/types";
import type { FieldErrors } from "../settings/apiErrors";
import { keyFor, type Execution } from "./types";

const OUTCOME: Record<Execution["outcome"], [string, string]> = {
  succeeded: ["badge-success", "Succeeded in sandbox"],
  rejected: ["badge-danger", "Refused by sandbox"],
  failed: ["badge-warning", "No answer from sandbox"],
  dispatched: ["badge-warning", "No answer from sandbox"],
};

// The executor step of an executable proposal: run it once through its mock adapter, see every pre-execution check,
// and retry safely with the same idempotency key. The server checks everything again; this only shows the result.
export function SandboxRun({ base, proposal, canRun, fmtTime, onChanged }: {
  base: string; proposal: Proposal; canRun: boolean;
  fmtTime: (iso: string) => string; onChanged: (p: Proposal) => void;
}) {
  const p = proposal;
  const [view, setView] = useState<Execution | null>(null);
  const [key, setKey] = useState(() => keyFor(p.id));
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [notice, setNotice] = useState<{ cls: string; text: string } | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});

  const executionId = p.execution?.execution_id;
  useEffect(() => {
    setChecks(null); setNotice(null); setErrors({}); setConfirm(false);
    if (!executionId) { setView(null); setKey(keyFor(p.id)); return; }
    api<Execution>(`${base}/sandbox/executions/${executionId}`).then((r) => {
      if (r.ok) { setView(r.data); setKey(r.data.idempotency_key); }
    });
  }, [base, p.id, executionId]);

  const refresh = async () => {
    const fresh = await api<Proposal>(`${base}/approvals/${p.id}`);
    if (fresh.ok) onChanged(fresh.data);
  };

  // A retry of a final outcome needs no new confirmation: it cannot send anything again.
  const run = async (retry = false) => {
    if (!confirm && !retry) return setErrors({ confirm: "Confirm that this runs only in the sandbox." });
    setBusy(true); setNotice(null); setErrors({});
    const r = await fetch(`${base}/sandbox/executions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal_id: p.id, payload_sha256: p.payload_sha256, idempotency_key: key, confirm_sandbox: true }),
    });
    const body = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.ok) {
      const e = body as Execution;
      setView(e); setChecks(null); setConfirm(false);
      if (e.replayed) setNotice({ cls: "notice", text: "Replayed: the stored outcome was returned and the adapter was not called again." });
      else if (e.outcome === "failed") setNotice({ cls: "badge badge-warning block-badge", text: "The sandbox did not answer. Press Retry: the same key is sent, so the action still happens at most once." });
      await refresh();
      return;
    }
    const detail = (body as { detail?: unknown }).detail;
    if (r.status === 409 && detail && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as { message?: string; checks?: Check[] };
      setChecks(d.checks ?? null);
      setNotice({ cls: "badge badge-danger block-badge", text: d.message ?? "Not executed." });
    } else if (r.status === 422 && Array.isArray(detail)) {
      setNotice({ cls: "badge badge-danger block-badge", text: "The request was not valid. Reload the proposal and try again." });
    } else {
      setNotice({ cls: "badge badge-danger block-badge", text: typeof detail === "string" ? detail : r.status === 403 ? "Only an owner can run an action." : `Run failed (HTTP ${r.status}).` });
    }
    await refresh();
  };

  const final = view?.final ?? false;
  const shownChecks = checks ?? view?.log.find((x) => x.event === "dispatched")?.checks ?? null;
  const [badgeCls, badgeText] = view ? OUTCOME[view.outcome] : ["", ""];

  return (
    <div className="sandbox-run" aria-labelledby="sandbox-title">
      <div className="sandbox-head">
        <h3 id="sandbox-title" className="ready-subtitle">Sandbox execution</h3>
        <span className="badge badge-warning">Sandbox only</span>
      </div>

      {view && (
        <div className={`sandbox-outcome outcome-${view.outcome}`}>
          <div className="sandbox-outcome-head">
            <span className={`badge ${badgeCls}`}>{badgeText}</span>
            {view.reference && <strong className="sandbox-ref">{view.reference}</strong>}
          </div>
          {typeof view.result?.note === "string" && <p className="sandbox-note">{view.result.note}</p>}
          <dl className="facts review-facts">
            <div><dt>Adapter</dt><dd>{view.adapter_label}</dd></div>
            <div><dt>Idempotency key</dt><dd><code className="hash">{view.idempotency_key}</code></dd></div>
            <div><dt>Sent by</dt><dd>{view.created_by} · {fmtTime(view.created_at)}</dd></div>
            {view.completed_at && <div><dt>Outcome recorded</dt><dd>{fmtTime(view.completed_at)}</dd></div>}
            <div><dt>Attempts</dt><dd>{view.attempts} sent{view.replays ? ` · ${view.replays} retr${view.replays === 1 ? "y" : "ies"} replayed` : ""}</dd></div>
          </dl>
          <details className="path-detail">
            <summary>Redacted request and result (as logged)</summary>
            <pre className="payload-json">{JSON.stringify({ request: view.request, result: view.result }, null, 2)}</pre>
          </details>
        </div>
      )}

      {shownChecks && (
        <details className="path-detail" open={!!checks}>
          <summary>Pre-execution checks: {shownChecks.filter((c) => c.ok).length} of {shownChecks.length} passed</summary>
          <ul className="ready-list release-checks">
            {shownChecks.map((c) => (
              <li key={c.code} className={`ready-item ${c.ok ? "is-present" : "is-missing"}`}>
                <span className="ready-mark" aria-hidden="true">{c.ok ? "+" : "!"}</span>
                <div className="ready-body">
                  <div className="release-line"><span className="ready-label">{CHECK_LABELS[c.code] ?? c.code}</span><span className="ready-word">{c.ok ? "Passed" : "Failed"}</span></div>
                  <p className="ready-finding">{c.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}

      {notice && <p className={notice.cls} role={notice.cls === "notice" ? "status" : "alert"}>{notice.text}</p>}

      {canRun ? (
        <div className="sandbox-actions">
          {!final && (
            <>
              <p className="muted small">Idempotency key: <code className="hash">{key}</code></p>
              <label className={`check-line${errors.confirm ? " has-error" : ""}`}>
                <input type="checkbox" checked={confirm} onChange={(e) => { setConfirm(e.target.checked); setErrors({}); }} />
                <span>I understand this runs only in the sandbox: no real {p.kind === "supplier_payment" ? "money moves" : p.kind === "financing_application" ? "lender receives it" : "message is sent"}.</span>
              </label>
              {errors.confirm && <span className="field-error" role="alert">{errors.confirm}</span>}
            </>
          )}
          <div className="form-actions">
            {!final && (
              <button type="button" className="btn btn-primary" onClick={() => run()} disabled={busy}>
                {busy ? "Checking and sending…" : view ? "Retry with the same key" : "Run in sandbox"}
              </button>
            )}
            {final && (
              <button type="button" className="btn btn-ghost" onClick={() => run(true)} disabled={busy}>
                {busy ? "Retrying…" : "Retry with the same key"}
              </button>
            )}
          </div>
        </div>
      ) : (
        !view && <p className="muted small">Only an owner can run an approved action in the sandbox.</p>
      )}
    </div>
  );
}

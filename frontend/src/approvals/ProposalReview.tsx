import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { SandboxRun } from "../executor/SandboxRun";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { Field } from "../settings/fields";
import { subtitle } from "./ApprovalInbox";
import { CHECK_LABELS, STATE_BADGE, isoDateIn, placeErrors, statusLine, textProblem, type Check, type Policy, type Proposal } from "./types";

const EVENT_LABEL = { approved: "Approved", rejected: "Rejected", withdrawn: "Withdrawn", executable: "Released as executable" } as const;

// Review of one exact proposal: what will happen, its hash, the gates for its tier, the decision history, and the
// actions the signed-in user may take. The server re-checks every rule; this only shows them and gives early feedback.
export function ProposalReview({ base, proposal, policy, canRun, money, fmtTime, timezone, onChanged, onClose }: {
  base: string; proposal: Proposal; policy: Policy | null; canRun: boolean;
  money: (minor: number, currency: string) => string; timezone: string | undefined;
  fmtTime: (iso: string) => string; onChanged: (p: Proposal) => void; onClose: () => void;
}) {
  const url = `${base}/approvals`;
  const p = proposal;
  const today = isoDateIn(timezone);                                // business timezone, as the server checks it
  const proposedOn = isoDateIn(timezone, new Date(p.created_at));
  const [mode, setMode] = useState<"none" | "approve" | "reject" | "withdraw">("none");
  const [f, setF] = useState({ confirm: false, method: "", reference: "", checked_on: isoDateIn(timezone), password: "", reason: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { setMode("none"); setErrors({}); setChecks(null); setNotice(null); setF((x) => ({ ...x, password: "", reason: "", confirm: false })); }, [p.id]);
  const set = (k: keyof typeof f, v: string | boolean) => { setF((x) => ({ ...x, [k]: v })); setErrors((e) => ({ ...e, [k]: "", _form: "" })); };

  const [badgeCls, badgeText] = STATE_BADGE[p.state];
  const s = statusLine(p);
  const d = p.payload as Record<string, unknown>;
  const left = Date.parse(p.expires_at) - Date.now();
  const lastChecks = checks ?? p.decisions.find((x) => x.event === "executable")?.checks ?? null;

  const post = async (path: string, body: unknown) => {
    setBusy(true);
    const r = await api<Proposal>(`${url}/${p.id}/${path}`, "POST", body);
    setBusy(false);
    if (!r.ok) {
      const placed = placeErrors(r.errors, ["reason", "password", "verification"]);
      // A failed re-authentication belongs under the password field.
      if (placed._form?.startsWith("Re-authentication failed")) { placed.password = placed._form; delete placed._form; }
      setErrors(placed);
      return;
    }
    setMode("none");
    setF((x) => ({ ...x, password: "", reason: "" }));
    onChanged(r.data);
  };

  const approve = (e: FormEvent) => {
    e.preventDefault();
    const errs: FieldErrors = {};
    if (!f.confirm) errs.confirm = "Confirm that you reviewed this exact proposal.";
    const body: Record<string, unknown> = { decision: "approve", payload_sha256: p.payload_sha256 };
    if (p.tier === "T3") {
      if (!f.method) errs.method = "Choose how you verified it.";
      const ref = textProblem(f.reference, 3, 120);
      if (ref) errs.reference = ref;
      if (!f.checked_on) errs.checked_on = "Enter the date you verified it.";
      else if (f.checked_on > today) errs.checked_on = "The verification date cannot be in the future.";
      else if (f.checked_on < proposedOn) errs.checked_on = `It cannot be before the proposal date (${proposedOn}).`;
      if (!f.password) errs.password = "Re-enter your password to approve a T3 action.";
      Object.assign(body, { password: f.password, verification: { method: f.method, reference: f.reference, checked_on: f.checked_on } });
    }
    if (Object.values(errs).some(Boolean)) return setErrors(errs);
    post("decision", body);
  };

  const withReason = (e: FormEvent) => {
    e.preventDefault();
    const r = textProblem(f.reason, 3, 300);
    if (r) return setErrors({ reason: r });
    post(mode === "reject" ? "decision" : "withdraw", mode === "reject"
      ? { decision: "reject", payload_sha256: p.payload_sha256, reason: f.reason }
      : { payload_sha256: p.payload_sha256, reason: f.reason });
  };

  const release = async () => {
    setBusy(true);
    setNotice(null);
    const r = await fetch(`${url}/${p.id}/release`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ payload_sha256: p.payload_sha256 }) });
    const body = await r.json().catch(() => ({}));
    setBusy(false);
    if (r.ok) { setChecks((body as Proposal).decisions.find((x) => x.event === "executable")?.checks ?? null); onChanged(body as Proposal); return; }
    const detail = (body as { detail?: { message?: string; checks?: Check[] } | string }).detail;
    if (detail && typeof detail === "object") { setChecks(detail.checks ?? null); setNotice(detail.message ?? "Not executable."); }
    else setNotice(typeof detail === "string" ? detail : `Release failed (HTTP ${r.status}).`);
    const fresh = await api<Proposal>(`${url}/${p.id}`);
    if (fresh.ok) onChanged(fresh.data);
  };

  return (
    <section className="card review-card" aria-labelledby="review-title" id="proposal-review">
      <div className="card-head">
        <div className="review-heading">
          <span className={`tier-chip tier-${p.tier}`}>{p.tier}</span>
          <h2 id="review-title" className="section-title">{p.title}</h2>
        </div>
        <button type="button" className="btn btn-ghost btn-small" onClick={onClose}>Close</button>
      </div>
      <p className="inbox-sub">{subtitle(p, money)}</p>
      <div className="review-state">
        <span className={`badge ${badgeCls}`}>{badgeText}</span>
        <span className={`inbox-status ${s.cls}`}>{s.text}</span>
      </div>
      {p.fraud_holds.length > 0 && (p.state === "pending" || p.state === "approved" || (p.state === "executable" && !p.execution)) && (
        <p className="badge badge-danger block-badge" role="alert">
          On hold: {p.fraud_holds.map((h) => h.title).join("; ")}. It cannot be {p.state === "executable" ? "run in the sandbox" : "approved or released"} until the flag is cleared in the{" "}
          <Link className="link" to="/fraud-monitor">Fraud Monitor</Link>. Suspected activity only, not a finding of fraud.
        </p>
      )}
      {!p.integrity.payload_hash_matches && (
        <p className="badge badge-danger block-badge" role="alert">The stored proposal does not match its hash. It cannot be approved or released.</p>
      )}

      <div className="review-columns">
        <div>
          <h3 className="ready-subtitle">Exact action</h3>
          <dl className="facts review-facts">
            {Object.entries(describePayload(p, d, money)).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
            <div><dt>Proposed by</dt><dd>{p.proposed_by.name} ({p.proposed_by.role}) · {fmtTime(p.created_at)}</dd></div>
            <div><dt>Valid until</dt><dd>{fmtTime(p.expires_at)}{left > 0 && (p.state === "pending" || p.state === "approved") ? ` (${hoursLeft(left)} left)` : ""}</dd></div>
            <div><dt>Payload hash (SHA-256)</dt><dd><code className="hash">{p.payload_sha256}</code></dd></div>
          </dl>
          <details className="path-detail">
            <summary>Stored payload (JSON)</summary>
            <pre className="payload-json">{JSON.stringify(p.payload, null, 2)}</pre>
          </details>
        </div>
        <div>
          <h3 className="ready-subtitle">Gates for {p.tier} ({p.policy_version})</h3>
          <ul className="ready-steps">{p.gates.map((g) => <li key={g}>{g}</li>)}</ul>
          <h3 className="ready-subtitle">Decisions</h3>
          {p.decisions.length === 0 && <p className="muted small">No decision yet.</p>}
          <ol className="timeline">
            {p.decisions.map((x) => (
              <li key={x.event + x.at} className="timeline-item">
                <span className={`timeline-dot ${x.event === "rejected" || x.event === "withdrawn" ? "dot-conflict" : ""}`} aria-hidden="true" />
                <div className="timeline-body">
                  <span className="timeline-text">{EVENT_LABEL[x.event]} by {x.actor} ({x.actor_role})</span>
                  <span className="muted small">
                    {fmtTime(x.at)}{x.reauthenticated ? " · re-authenticated" : ""}{x.reason ? ` · ${x.reason}` : ""}
                  </span>
                  {x.verification && <span className="muted small">Verified: {x.verification.method_label ?? x.verification.method} · {x.verification.reference} · {x.verification.checked_on}</span>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>

      {lastChecks && (
        <div>
          <h3 className="ready-subtitle">Release check</h3>
          <ul className="ready-list release-checks">
            {lastChecks.map((c) => (
              <li key={c.code} className={`ready-item ${c.ok ? "is-present" : "is-missing"}`}>
                <span className="ready-mark" aria-hidden="true">{c.ok ? "+" : "!"}</span>
                <div className="ready-body">
                  <div className="release-line"><span className="ready-label">{CHECK_LABELS[c.code] ?? c.code}</span><span className="ready-word">{c.ok ? "Passed" : "Failed"}</span></div>
                  <p className="ready-finding">{c.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {notice && <p className="badge badge-danger block-badge" role="alert">{notice}</p>}
      {p.state === "executable" && (
        <SandboxRun base={base} proposal={p} canRun={canRun} fmtTime={fmtTime} onChanged={onChanged} />
      )}

      {mode === "none" && (
        <div className="form-actions">
          {p.can.approve && <button type="button" className="btn btn-primary" onClick={() => setMode("approve")} disabled={!p.integrity.payload_hash_matches}>Approve…</button>}
          {p.can.reject && <button type="button" className="btn btn-ghost" onClick={() => setMode("reject")}>Reject…</button>}
          {p.can.withdraw && <button type="button" className="btn btn-ghost" onClick={() => setMode("withdraw")}>Withdraw…</button>}
          {p.can.release && <button type="button" className="btn btn-primary" onClick={release} disabled={busy}>{busy ? "Checking…" : "Run release check"}</button>}
          {p.state === "pending" && !p.can.approve && p.can.reject && p.second_reviewer_required && (
            <p className="muted small">You proposed this {p.tier} action, so another owner must approve it.</p>
          )}
          {p.state === "pending" && !p.can.reject && <p className="muted small">Only an owner can approve or reject.</p>}
        </div>
      )}

      {mode === "approve" && (
        <form className="stack-form decide-form" onSubmit={approve} noValidate>
          {p.tier === "T3" && (
            <>
              <div className="form-grid">
                <Field label="How it was verified" error={errorFor(errors, "method", "verification.method")}>
                  <select value={f.method} onChange={(e) => set("method", e.target.value)}>
                    <option value="">Choose…</option>
                    {Object.entries(policy?.verification_methods ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                <Field label="Verified on" error={errorFor(errors, "checked_on", "verification.checked_on")}>
                  <input type="date" value={f.checked_on} min={proposedOn} max={today} onChange={(e) => set("checked_on", e.target.value)} />
                </Field>
              </div>
              <Field label="Verification note" error={errorFor(errors, "reference", "verification.reference")} hint="Who you spoke to or where the confirmation is kept. No account numbers.">
                <input value={f.reference} maxLength={140} autoComplete="off" onChange={(e) => set("reference", e.target.value)} />
              </Field>
              <Field label="Your password (re-authentication)" error={errorFor(errors, "password")}>
                <input type="password" autoComplete="off" value={f.password} onChange={(e) => set("password", e.target.value)} />
              </Field>
            </>
          )}
          <label className={`check-line${errors.confirm ? " has-error" : ""}`}>
            <input type="checkbox" checked={f.confirm} onChange={(e) => set("confirm", e.target.checked)} />
            <span>I reviewed this exact proposal (hash {p.payload_sha256.slice(0, 12)}…) and approve it.</span>
          </label>
          {errors.confirm && <span className="field-error" role="alert">{errors.confirm}</span>}
          {errors._form && <p className="badge badge-danger block-badge" role="alert">{errors._form}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Approving…" : `Approve ${p.tier}`}</button>
            <button type="button" className="btn btn-ghost" onClick={() => { setMode("none"); setErrors({}); }}>Cancel</button>
          </div>
        </form>
      )}

      {(mode === "reject" || mode === "withdraw") && (
        <form className="stack-form decide-form" onSubmit={withReason} noValidate>
          <Field label={mode === "reject" ? "Reason for rejecting" : "Reason for withdrawing"} error={errorFor(errors, "reason")}>
            <input value={f.reason} maxLength={320} onChange={(e) => set("reason", e.target.value)} />
          </Field>
          {errors._form && <p className="badge badge-danger block-badge" role="alert">{errors._form}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>{mode === "reject" ? "Reject" : "Withdraw"}</button>
            <button type="button" className="btn btn-ghost" onClick={() => { setMode("none"); setErrors({}); }}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}

function hoursLeft(ms: number): string {
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function describePayload(p: Proposal, d: Record<string, unknown>, money: (minor: number, currency: string) => string): Record<string, string> {
  const amt = (k: string) => money(d[k] as number, d.currency as string);
  switch (p.kind) {
    case "invoice_reminder":
      return { Customer: String(d.customer), Invoice: `${d.document_number}, due ${d.due_date}`, Outstanding: amt("outstanding_minor"),
               Channel: d.channel === "sms" ? "SMS (sandbox)" : "Email (sandbox)", Message: String(d.message) };
    case "supplier_payment": {
      const r = d.payment_review as { decision: string; as_of: string } | null;
      return { Supplier: String(d.supplier), Bill: String(d.document_number), Amount: amt("amount_minor"),
               "Outstanding when proposed": amt("outstanding_minor"), "Pay from": String(d.from_account),
               "Payment Review": r ? `${r.decision.replace(/_/g, " ")} (as of ${r.as_of})` : "None linked", Delivery: "Sandbox only" };
    }
    case "financing_application":
      return { Product: `${d.product} · ${d.provider}`, Amount: amt("amount_minor"), "Needed by": String(d.needed_by),
               Purpose: String(d.purpose).replace(/_/g, " "), Recipient: `${d.recipient} (illustrative catalogue)`,
               "Readiness check": `${String(d.readiness_check_id).slice(0, 8)}… (ready)` };
  }
}

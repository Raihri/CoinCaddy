import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { isoDateIn, textProblem } from "../approvals/types";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { Field } from "../settings/fields";
import { RULE_BADGE, STATE_BADGE, type FlagDetail, type RecordSnap, type RulesInfo } from "./types";

type Money = (minor: number, currency: string) => string;
type Mode = "none" | "confirm" | "reject" | "defer";

const COMPARE_LABELS: Record<string, [string, string]> = {
  duplicate_payment: ["New payment", "Earlier payment"],
  destination_change: ["New destination", "Previous destination"],
  lookalike_payee: ["Payment to the lookalike name", "Established payee"],
  unusual_payee_amount: ["This payment", "Earlier payments to this payee"],
  split_near_limit: ["Payments in the split", ""],
  paid_without_settlement: ["Recorded payment", "Invoice or bill"],
};
const DECISION_LABEL = { confirmed: "Marked for investigation", rejected: "Cleared", deferred: "Deferred" } as const;

const shortId = (id: string) => id.slice(0, 8);

// "Why this was flagged" (images/07-fraud-monitor.png): rule badge, the compared records side by side, the two bounded
// cash outcomes, the held payments, the review history, and the reviewer's actions. The server re-checks every rule.
export function FlagPanel({ base, flagId, rulesInfo, timezone, money, onChanged }: {
  base: string; flagId: string; rulesInfo: RulesInfo | null; timezone: string | undefined; money: Money; onChanged: () => void;
}) {
  const [flag, setFlag] = useState<FlagDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("none");
  const today = isoDateIn(timezone);
  const blank = { reason: "", defer_until: "", method: "", contact_source: "", predates: false, checked_on: today };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setFlag(null); setLoadError(null); setMode("none"); setErrors({});
    api<FlagDetail>(`${base}/fraud/flags/${flagId}`).then((r) => (r.ok ? setFlag(r.data) : setLoadError(r.message)));
  }, [base, flagId]);

  if (loadError) return <section className="card"><p className="badge badge-danger block-badge" role="alert">{loadError}</p></section>;
  if (!flag) return <section className="card"><h2 className="section-title">Why this was flagged</h2><p className="muted">Loading…</p></section>;

  const set = (k: keyof typeof f, v: string | boolean) => { setF((x) => ({ ...x, [k]: v })); setErrors((e) => ({ ...e, [k]: "", _form: "" })); };
  const [stateCls, stateText] = STATE_BADGE[flag.state];
  const [newLabel, oldLabel] = COMPARE_LABELS[flag.rule];
  const subjects = flag.evidence.subjects;
  const compared = flag.evidence.compared.slice(-3);
  const changedOn = flag.rule === "destination_change" ? subjects[0]?.date ?? today : undefined;
  const maxDefer = addDays(today, rulesInfo?.max_defer_days ?? 30);
  const open = (m: Mode) => { setMode(m); setF({ ...blank, checked_on: today }); setErrors({}); };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: FieldErrors = {};
    const reason = textProblem(f.reason, 3, 300, true);
    if (reason) errs.reason = reason;
    const body: Record<string, unknown> = { decision: mode, reason: f.reason };
    if (mode === "defer") {
      if (!f.defer_until) errs.defer_until = "Choose the date to review this flag again.";
      else if (f.defer_until <= today || f.defer_until > maxDefer) errs.defer_until = `Choose a date after today and no later than ${fmtDate(maxDefer)}.`;
      body.defer_until = f.defer_until;
    }
    if (mode === "reject" && flag.can.verification_required) {
      if (!f.method) errs.method = "Choose how the change was verified.";
      const src = textProblem(f.contact_source, 3, 120, true);
      if (src) errs.contact_source = src;
      if (!f.predates) errs.predates = "Use a contact that was on file before the change. Details in the change request are not independent.";
      if (!f.checked_on) errs.checked_on = "Enter the date it was verified.";
      else if (f.checked_on > today) errs.checked_on = "The date cannot be in the future.";
      else if (changedOn && f.checked_on < changedOn) errs.checked_on = `It cannot be before the change (${fmtDate(changedOn)}).`;
      body.verification = { method: f.method, contact_source: f.contact_source, contact_predates_change: f.predates, checked_on: f.checked_on };
    }
    if (Object.values(errs).some(Boolean)) return setErrors(errs);
    setBusy(true);
    const r = await api<FlagDetail>(`${base}/fraud/flags/${flag.id}/reviews`, "POST", body);
    setBusy(false);
    if (!r.ok) {
      const placed: FieldErrors = {};
      for (const [k, v] of Object.entries(r.errors)) {
        const key = k.replace(/^verification\./, "").replace(/^contact_predates_change$/, "predates");
        if (["reason", "defer_until", "method", "contact_source", "predates", "checked_on"].includes(key)) placed[key] ??= v;
        else placed._form ??= v;
      }
      return setErrors(placed);
    }
    setFlag(r.data);
    setMode("none");
    onChanged();
  };

  return (
    <section className="card flag-card" aria-labelledby="flag-title" id="flag-panel">
      <h2 id="flag-title" className="section-title">Why this was flagged</h2>
      <div className="badges">
        <span className={`rule-badge ${flag.severity === "high" ? "rule-high" : "rule-medium"}`}>{RULE_BADGE[flag.rule]}</span>
        <span className={`badge ${stateCls}`}>{stateText}</span>
        {flag.review_due && <span className="badge badge-warning">Review date reached</span>}
      </div>
      <p className="flag-reason">{flag.reason}</p>

      <div className={`compare ${oldLabel ? "" : "compare-single"}`}>
        <div className="compare-side">
          <h3 className="compare-label">{newLabel}</h3>
          {subjects.map((s) => <RecordLine key={s.id} r={s} money={money} />)}
        </div>
        {oldLabel && (
          <div className="compare-side">
            <h3 className="compare-label">{oldLabel}</h3>
            {compared.map((s) => <RecordLine key={s.id} r={s} money={money} />)}
          </div>
        )}
      </div>

      <div>
        <h3 className="ready-subtitle">Impact on cash outlook</h3>
        <CashImpact flag={flag} money={money} />
      </div>

      {flag.holds.length > 0 && (
        <div>
          <h3 className="ready-subtitle">{flag.holding ? "Proposed payments on hold" : "Payments this flag held"}</h3>
          <ul className="hold-list">
            {flag.holds.map((h) => (
              <li key={h.proposal_id}>
                <span className="nowrap num">{money(h.amount_minor, h.currency)}</span> to {h.supplier} for {h.document_number} from {h.from_account}
                <span className="muted small"> · proposed by {h.proposed_by_name} · </span>
                <Link className="link" to="/approvals-audit">Open in Approvals</Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {flag.reviews.length > 0 && (
        <div>
          <h3 className="ready-subtitle">Reviews</h3>
          <ol className="timeline">
            {flag.reviews.map((r) => (
              <li key={r.at} className="timeline-item">
                <span className={`timeline-dot ${r.decision === "confirmed" ? "dot-failed" : r.decision === "deferred" ? "dot-conflict" : ""}`} aria-hidden="true" />
                <div className="timeline-body">
                  <span className="timeline-text">{DECISION_LABEL[r.decision]} by {r.reviewer} ({r.reviewer_role}){r.defer_until ? ` until ${fmtDate(r.defer_until)}` : ""}</span>
                  <span className="muted small">{new Date(r.at).toLocaleString("en-GB", { timeZone: timezone })} · {r.reason}</span>
                  {r.verification && <span className="muted small">Verified: {r.verification.method_label ?? r.verification.method} · {r.verification.contact_source} · {fmtDate(r.verification.checked_on)}</span>}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      <p className="muted small">{flag.verdict} Rule set {flag.rules_version}. Compared record IDs: {[...flag.subject_refs, ...flag.compared_refs].map((r) => shortId(r.id)).join(", ")}.</p>

      {mode === "none" && (flag.can.confirm || flag.can.defer) && (
        <>
          <div className="flag-actions">
            <button type="button" className="btn btn-danger" onClick={() => open("confirm")}>Mark for investigation</button>
            <button type="button" className="btn btn-primary" onClick={() => open("reject")} disabled={!flag.can.reject}>
              {flag.can.verification_required ? "Record verification" : "Clear as legitimate"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => open("defer")}>Defer</button>
          </div>
          {flag.can.reject_blocked_reason && <p className="muted small" role="note">{flag.can.reject_blocked_reason}</p>}
          {flag.can.reject && flag.can.reject_needs.length > 0 && <p className="muted small">Clearing needs: {flag.can.reject_needs.join("; ")}.</p>}
        </>
      )}
      {(flag.state === "confirmed" || flag.state === "cleared") && (
        <p className="notice">{flag.state === "cleared" ? "Cleared. The original records are unchanged and any hold from this flag is lifted." :
          "Marked for investigation. Held payments stay on hold; reject or withdraw them in Approvals."}</p>
      )}

      {mode !== "none" && (
        <form className="stack-form decide-form" onSubmit={submit} noValidate>
          <h3 className="ready-subtitle">{mode === "confirm" ? "Mark for investigation" : mode === "defer" ? "Defer the review" : flag.can.verification_required ? "Record independent verification" : "Clear as legitimate"}</h3>
          {mode === "confirm" && <p className="muted small">The flag stays as suspected activity under investigation. Held payments stay on hold. This is final.</p>}
          {mode === "reject" && <p className="muted small">Clearing records that you checked this and found it legitimate. It lifts any hold from this flag and is final.</p>}
          {mode === "reject" && flag.can.verification_required && (
            <>
              <div className="form-grid">
                <Field label="How it was verified" error={errors.method}>
                  <select value={f.method} onChange={(e) => set("method", e.target.value)}>
                    <option value="">Choose…</option>
                    {Object.entries(rulesInfo?.verification_methods ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </Field>
                <Field label="Verified on" error={errors.checked_on}>
                  <input type="date" value={f.checked_on} min={changedOn} max={today} onChange={(e) => set("checked_on", e.target.value)} />
                </Field>
              </div>
              <Field label="Contact used and where it came from" error={errors.contact_source} hint="For example: phone number in the supplier file since 2024. No account or phone numbers.">
                <input value={f.contact_source} maxLength={140} autoComplete="off" onChange={(e) => set("contact_source", e.target.value)} />
              </Field>
              <label className={`check-line${errors.predates ? " has-error" : ""}`}>
                <input type="checkbox" checked={f.predates} onChange={(e) => set("predates", e.target.checked)} />
                <span>The contact was on file before this change, not taken from the change request.</span>
              </label>
              {errors.predates && <span className="field-error" role="alert">{errors.predates}</span>}
            </>
          )}
          {mode === "defer" && (
            <Field label="Review again on" error={errors.defer_until}>
              <input type="date" value={f.defer_until} min={addDays(today, 1)} max={maxDefer} onChange={(e) => set("defer_until", e.target.value)} />
            </Field>
          )}
          <Field label="Reason" error={errorFor(errors, "reason")}>
            <input value={f.reason} maxLength={320} autoComplete="off" onChange={(e) => set("reason", e.target.value)} />
          </Field>
          {errors._form && <p className="badge badge-danger block-badge" role="alert">{errors._form}</p>}
          <div className="form-actions">
            <button type="submit" className={`btn ${mode === "confirm" ? "btn-danger" : "btn-primary"}`} disabled={busy}>{busy ? "Saving…" : "Save review"}</button>
            <button type="button" className="btn btn-ghost" onClick={() => { setMode("none"); setErrors({}); }}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
}

function RecordLine({ r, money }: { r: RecordSnap; money: Money }) {
  if (r.type === "payee_destination") {
    return (
      <div className="compare-record">
        <strong>{r.institution} {r.masked}</strong>
        <span className="muted small">{r.kind === "mobile_wallet" ? "Mobile wallet" : "Bank"} · {fmtDate(r.date)} · {String(r.change_source ?? "").replace(/_/g, " ")}</span>
      </div>
    );
  }
  if (r.type === "obligation" || r.type === "party") {
    return (
      <div className="compare-record">
        <strong>{r.reference ?? r.payee}</strong>
        <span className="muted small">{r.type === "obligation" ? <Link className="link" to={`/records/obligations/${r.id}`}>Open the bill or invoice</Link> : `Payee since ${fmtDate(r.date)}`}</span>
      </div>
    );
  }
  const where = r.type === "action_proposal" ? <Link className="link" to="/approvals-audit">Proposed payment {shortId(r.id)}</Link>
    : r.type === "ledger_transaction" && r.account_id ? <Link className="link" to={`/records/accounts/${r.account_id}`}>{r.account} · {shortId(r.id)}</Link>
    : <span>Payment record {shortId(r.id)}</span>;
  return (
    <div className="compare-record">
      <strong className="num">{r.amount_minor !== undefined && r.currency ? money(r.amount_minor, r.currency) : "—"} · {fmtDate(r.date)}</strong>
      <span className="muted small">{[r.payee, r.reference ? `Ref ${r.reference}` : null].filter(Boolean).join(" · ")}</span>
      <span className="small">{where}</span>
    </div>
  );
}

function CashImpact({ flag, money }: { flag: FlagDetail; money: Money }) {
  const co = flag.cash_outcomes;
  if (!co.available) return <p className="muted">{co.reason}</p>;
  const [a, b] = co.outcomes;
  const line = (o: typeof a) => `${o.label}: projected low ${money(o.lowest_expected.amount_minor, co.currency)} on ${fmtDate(o.lowest_expected.date)}`;
  return (
    <div className="impact">
      <p className="impact-text">{line(a)}. {line(b)}.{flag.holding ? " Positive payment guidance stays paused." : ""}</p>
      <ul className="outcome-list">
        {[a, b].map((o) => (
          <li key={o.label} className="outcome">
            <span className="outcome-label">{o.label}</span>
            <span className="outcome-value num">{money(o.lowest_expected.amount_minor, co.currency)}</span>
            <span className="muted small">Cautious path {money(o.lowest_cautious.amount_minor, co.currency)}</span>
            {co.reserve_minor !== null && (
              <span className={`small ${o.below_reserve ? "text-danger" : "text-success"}`}>{o.below_reserve ? "Below" : "Above"} the {money(co.reserve_minor, co.currency)} reserve</span>
            )}
          </li>
        ))}
      </ul>
      <details className="path-detail">
        <summary>Assumptions and source</summary>
        <ul className="ready-steps">{co.assumptions.map((x) => <li key={x}>{x}</li>)}</ul>
        <p className="muted small">Bounded on today's Cash Outlook inputs ({co.calc_version}, as of {fmtDate(co.as_of)}, inputs {co.inputs_sha256.slice(0, 12)}…). Nothing is saved or changed.</p>
      </details>
    </div>
  );
}

function addDays(iso: string, n: number): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

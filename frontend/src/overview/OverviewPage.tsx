import { useCallback, useEffect, useRef, useState } from "react";
import { RegionNote } from "../region/RegionNote";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import { exponentOf } from "../settings/fields";

// Layout follows images/01-overview.png (Priority actions list); styling follows design.md.
// Only the alerts part of the Overview exists so far: the KPI tiles and the outlook mini chart are not built here.
type Severity = "high" | "medium" | "low";
type RecordRef = { kind: string; id: string };
type Alert = {
  id: string; type: "reserve_breach" | "overdue_invoice" | "unusual_outflow" | "repayment_pressure"; severity: Severity;
  date: string; amount_minor: number; currency: string; cause: string; record_ids: RecordRef[];
  detection_count: number; first_detected_at: string;
};
type Run = { id: string; as_of: string; created_at: string; created_by: string; created: number; updated: number; unchanged: number; resolved: number };
type AlertList = { alerts: Alert[]; last_run: Run | null; today: string; checked_today: boolean };

const TITLES: Record<Alert["type"], string> = {
  reserve_breach: "Reserve shortfall ahead", overdue_invoice: "Overdue invoice",
  unusual_outflow: "Unusual outflow", repayment_pressure: "Repayment pressure",
};
const SEVERITY_LABEL: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low" };
const KIND_LABEL: Record<string, string> = { obligation: "Invoice or bill", account: "Account", ledger_transaction: "Transaction" };

function recordLink(r: RecordRef): string | null {
  if (r.kind === "obligation") return `/records/obligations/${r.id}`;
  if (r.kind === "account") return `/records/accounts/${r.id}`;
  return null;
}
const shortId = (id: string) => id.slice(0, 8);

export function OverviewPage() {
  const { me, active, options } = useSession();
  const [data, setData] = useState<AlertList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const autoRunFor = useRef<string | null>(null);
  const base = active ? `/api/businesses/${active.business_id}/alerts` : "";
  const canRun = active?.allowed_actions.includes("alerts.run") ?? false;

  const load = useCallback(async () => {
    if (!base) return;
    const r = await api<AlertList>(base);
    if (r.ok) { setData(r.data); setError(null); } else setError(r.message);
  }, [base]);
  useEffect(() => { setData(null); setError(null); setMessage(null); load(); }, [load]);

  const run = useCallback(async () => {
    setBusy(true); setMessage(null);
    const r = await api<{ created: number; resolved: number; alerts: Alert[]; last_run: Run }>(`${base}/run`, "POST", {});
    setBusy(false);
    if (!r.ok) { setError(r.message); return; }
    setMessage(r.data.created || r.data.resolved
      ? `Checked now: ${r.data.created} new, ${r.data.resolved} cleared.`
      : "Checked now: nothing new. Existing alerts were not duplicated.");
    await load();
  }, [base, load]);

  useEffect(() => {
    const businessId = active?.business_id;
    if (!businessId || !canRun || !data || autoRunFor.current === businessId) return;
    autoRunFor.current = businessId;
    void run();
  }, [active?.business_id, canRun, data, run]);

  const first = me?.user.display_name.split(" ")[0];
  const alerts = data?.alerts ?? [];
  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">{first ? `Hello, ${first}` : "Overview"}</h1>
          <p className="page-sub">Your business position and the decisions that need attention today</p>
        </div>
      </div>
      <RegionNote />

      <section className="card" aria-labelledby="alerts-title" style={{ marginTop: 16 }}>
        <div className="card-head">
          <h2 id="alerts-title" className="section-title">Priority actions</h2>
          {data && <span className="muted small">{alerts.length} open alert{alerts.length === 1 ? "" : "s"}</span>}
        </div>
        {error && <p className="badge badge-danger" role="alert">{error}</p>}
        {message && <p className="badge badge-success" role="status">{message}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {busy && <p className="muted small" role="status" aria-live="polite">Checking accepted records for alerts…</p>}
        {data && (
          <p className="muted small alert-checked">
            {data.last_run
              ? <>Last checked {new Date(data.last_run.created_at).toLocaleString("en-GB")} by {data.last_run.created_by} on data as of {fmtDate(data.last_run.as_of)}.
                  {!data.checked_today && " That is not today: figures may be out of date."}</>
              : "Alerts have not been checked yet."}
            {!data.last_run && !canRun && " An owner or bookkeeper can run the check."}
          </p>
        )}
        {data && data.last_run && alerts.length === 0 && (
          <div className="alert-card alert-low" role="status">
            <div className="alert-title">No open alerts</div>
            <p className="alert-cause">No reserve breach, overdue invoice, unusual outflow, or repayment pressure was found in the last check.</p>
          </div>
        )}
        <ul className="alert-list">
          {alerts.map((a) => (
            <li key={a.id} className={`alert-card alert-${a.severity}`}>
              <div className="alert-head">
                <span className="alert-title">{TITLES[a.type]}</span>
                <span className="badges">
                  <span className={`badge ${a.severity === "high" ? "badge-danger" : a.severity === "medium" ? "badge-warning" : "badge-info"}`}>
                    {SEVERITY_LABEL[a.severity]}
                  </span>
                </span>
              </div>
              <p className="alert-cause">{a.cause}</p>
              <dl className="alert-facts">
                <div><dt>{a.type === "reserve_breach" ? "Shortfall" : "Amount"}</dt>
                     <dd className="nowrap">{formatMoney(a.amount_minor, a.currency, exponentOf(options, a.currency))}</dd></div>
                <div><dt>Date</dt><dd className="nowrap">{fmtDate(a.date)}</dd></div>
                <div><dt>First seen</dt><dd className="nowrap">{fmtDate(a.first_detected_at.slice(0, 10))}</dd></div>
              </dl>
              <div className="alert-records">
                <span className="muted small">Supporting records</span>
                {a.record_ids.map((r) => {
                  const to = recordLink(r);
                  const label = `${KIND_LABEL[r.kind] ?? r.kind} ${shortId(r.id)}`;
                  return to ? <Link key={r.kind + r.id} className="chip" to={to} title={r.id}>{label}</Link>
                            : <span key={r.kind + r.id} className="chip" title={r.id}>{label}</span>;
                })}
              </div>
            </li>
          ))}
        </ul>
        {a11yNote(data)}
      </section>
    </>
  );
}

function a11yNote(data: AlertList | null) {
  if (!data) return null;
  return <p className="muted small" style={{ marginTop: 12 }}>
    Alerts are rule-based and use only accepted records and the Cash Outlook. <Link className="link" to="/cash-outlook">See the cash outlook</Link>
  </p>;
}

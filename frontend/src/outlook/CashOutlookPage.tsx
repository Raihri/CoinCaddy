import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { TimeSeriesChart, type LineSeries } from "../charts/Charts";
import { formatMoney } from "../money";
import { exponentOf } from "../settings/fields";

// Layout follows images/04-cash-outlook.png; styling follows design.md (solid #2347A5 line, dashed #2876B8 forecast,
// warning #B76E00 for the reserve floor).
const EXPECTED = "var(--primary)";
const CAUTIOUS = "var(--info)";
const RESERVE = "var(--warning)";

type Breach = { date: string; balance_minor: number; gap_minor: number } | null;
type CurrencyResult = {
  currency: string; reserve_minor: number | null; opening_expected_minor: number; opening_cautious_minor: number;
  daily: { date: string; expected_minor: number; cautious_minor: number }[];
  weekly: { week: number; end_date: string; expected_close_minor: number; cautious_close_minor: number; expected_min_minor: number; cautious_min_minor: number }[];
  lowest: Record<"expected" | "cautious", { date: string; amount_minor: number }>;
  lowest_30d: Record<"expected" | "cautious", { date: string; amount_minor: number }>;
  first_breach: Record<"expected" | "cautious", Breach>;
};
type OutlookEvent = { source: string; source_id: string; label: string; direction: string; currency: string; account_id: string;
                      assumed_account: boolean; amount_minor: number; expected_date: string | null; cautious_date: string | null; uncertain: boolean; note: string | null };
type Snapshot = {
  id: string; calc_version: string; as_of: string; inputs_sha256: string; created_by: string; created_at: string;
  inputs: { accounts: { id: string; label: string }[] };
  results: { horizon_days: number; rules: { cautious_delay_days: number; overdue_assumed_days: number }; currencies: CurrencyResult[];
             accounts: { id: string; label: string; currency: string; freshness: string; opening_expected_minor: number; opening_cautious_minor: number;
                         lowest_expected_minor: number; lowest_cautious_minor: number }[];
             events: OutlookEvent[]; evidence: { code: string; severity: "warning" | "info"; message: string; source_id?: string }[] };
  current: { today: string; is_today: boolean; inputs_unchanged: boolean };
};

const dayMonth = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });

export function CashOutlookPage() {
  const { active, options } = useSession();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ready">("loading");
  const [cur, setCur] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const base = active ? `/api/businesses/${active.business_id}/cash-outlook` : "";
  const canRun = active?.allowed_actions.includes("outlook.run") ?? false;

  const load = useCallback(async () => {
    if (!base) return;
    const r = await api<Snapshot>(`${base}/latest`);
    if (r.ok) { setSnap(r.data); setState("ready"); } else if (r.status === 404) setState("none"); else setMessage({ kind: "danger", text: r.message });
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const recalc = async () => {
    setBusy(true);
    const r = await api<Snapshot>(`${base}/snapshots`, "POST", {});
    setBusy(false);
    if (r.ok) { setSnap(r.data); setState("ready"); setMessage({ kind: "success", text: "Outlook recalculated from today's records." }); }
    else setMessage({ kind: "danger", text: r.message });
  };
  const verify = async () => {
    const r = await api<{ inputs_hash_matches: boolean; results_reproduce: boolean }>(`${base}/snapshots/${snap!.id}/verify`);
    if (r.ok) setMessage(r.data.inputs_hash_matches && r.data.results_reproduce
      ? { kind: "success", text: "Verified: the stored inputs match their hash and recalculating them gives exactly the same figures." }
      : { kind: "danger", text: "Verification failed: the stored figures do not follow from the stored inputs." });
  };

  const header = (
    <div className="page-header">
      <div>
        <h1 className="page-title">Cash outlook</h1>
        <p className="page-sub">See when cash may be tight and which assumptions drive the forecast</p>
      </div>
    </div>
  );
  if (state === "loading") return <>{header}<p className="muted">Loading…</p></>;
  if (state === "none" || !snap) return (
    <>
      {header}
      {message && <p className={`badge badge-${message.kind}`}>{message.text}</p>}
      <section className="card empty-state">
        <p>No outlook has been calculated yet.</p>
        {canRun
          ? <button type="button" className="btn btn-primary" disabled={busy} onClick={recalc}>{busy ? "Calculating…" : "Calculate outlook"}</button>
          : <p className="muted small">An owner or bookkeeper can calculate one.</p>}
      </section>
    </>
  );

  const res = snap.results;
  const c = res.currencies.find((x) => x.currency === cur) ?? res.currencies[0];
  const ccy = c?.currency ?? "BDT";
  const exp = exponentOf(options, ccy);
  const money = (m: number) => formatMoney(m, ccy, exp);
  const thousands = (m: number) => (m === 0 ? "0" : Math.round(m / 10 ** exp / 1000).toLocaleString("en-IN"));
  const horizonEnd = c ? c.daily[c.daily.length - 1].date : snap.as_of;
  const upcoming = res.events.filter((e) => e.currency === ccy && e.expected_date && e.expected_date <= horizonEnd);
  const notCounted = res.events.filter((e) => e.currency === ccy && !e.expected_date);
  // Time series: opening (as-of date), every day for 30 days, then each week's close to week 13.
  const points = c ? [
    { date: snap.as_of, expected: c.opening_expected_minor, cautious: c.opening_cautious_minor },
    ...c.daily.map((d) => ({ date: d.date, expected: d.expected_minor, cautious: d.cautious_minor })),
    ...c.weekly.filter((w) => w.end_date > horizonEnd).map((w) => ({ date: w.end_date, expected: w.expected_close_minor, cautious: w.cautious_close_minor })),
  ] : [];
  const weekEnds = new Set(c ? c.weekly.map((w) => w.end_date) : []);
  const series: LineSeries[] = [
    { name: "Expected", values: points.map((p) => p.expected), color: EXPECTED, marker: "circle" },
    { name: "Cautious", values: points.map((p) => p.cautious), color: CAUTIOUS, dashed: true, marker: "square" },
  ];

  return (
    <>
      {header}
      {message && <p className={`badge badge-${message.kind} block-badge`} role="status">{message.text}</p>}
      {!snap.current.inputs_unchanged && (
        <div className="stale-bar" role="status">
          <span>{snap.current.is_today ? "Records have changed since this outlook was calculated." : `This outlook is for ${fmtDate(snap.as_of)}; today is ${fmtDate(snap.current.today)}.`}</span>
          {canRun
            ? <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={recalc}>{busy ? "Calculating…" : "Recalculate"}</button>
            : <span className="muted small">Ask an owner or bookkeeper to recalculate.</span>}
        </div>
      )}

      {!c ? (
        <section className="card"><p>No account has a verified balance, so no path can be projected. See the evidence below.</p></section>
      ) : (
        <>
          <BreachBanner c={c} money={money} />
          {res.currencies.length > 1 && (
            <div className="tabs" role="tablist">
              {res.currencies.map((x) => <button key={x.currency} type="button" role="tab" aria-selected={x.currency === ccy}
                                                 className={`tab ${x.currency === ccy ? "is-active" : ""}`} onClick={() => setCur(x.currency)}>{x.currency}</button>)}
            </div>
          )}

          <div className="outlook-grid">
            <section className="card" aria-labelledby="forecast-title">
              <div className="card-head">
                <h2 id="forecast-title" className="section-title">13-week cash forecast</h2>
                <div className="legend-pills" aria-label="Legend">
                  <span className="legend-pill pill-expected"><span className="legend-line-swatch" style={{ borderColor: EXPECTED }} />Expected</span>
                  <span className="legend-pill pill-cautious"><span className="legend-line-swatch dashed" style={{ borderColor: CAUTIOUS }} />Cautious</span>
                </div>
              </div>
              <TimeSeriesChart dates={points.map((p) => p.date)} series={series} markDates={weekEnds}
                         reference={c.reserve_minor !== null ? { value: c.reserve_minor, label: "Reserve floor", color: RESERVE } : undefined}
                         format={money} axisFormat={thousands} unit={`${ccy === "BDT" ? "৳" : ccy} '000`}
                         xCaption="Business date · daily for 30 days, then weekly closing balance"
                         caption="13-week cash forecast over time: expected and cautious closing balances by date" />
            </section>

            <section className="card" aria-labelledby="moves-title">
              <h2 id="moves-title" className="section-title">Upcoming movements</h2>
              <ul className="movement-list mockup-moves">
                {upcoming.length === 0 && <li className="muted">No dated movements in the next 30 days.</li>}
                {upcoming.map((e) => (
                  <li key={e.source + e.source_id}>
                    <Link to={e.source === "obligation" ? `/records/obligations/${e.source_id}` : `/records/accounts/${e.account_id}`} className="movement"
                          title={e.note ?? undefined}>
                      <span className="movement-text">
                        <span className="movement-date">{dayMonth(e.expected_date!)}{e.uncertain && <span className="badge badge-warning tiny-badge">Uncertain</span>}</span>
                        <strong className="movement-title">{e.label}</strong>
                      </span>
                      <span className={`movement-amount num ${e.amount_minor < 0 ? "neg" : "pos-in"}`}>
                        {e.amount_minor < 0 ? "- " : "+ "}{money(Math.abs(e.amount_minor))}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
              {notCounted.length > 0 && (
                <p className="muted small">Not counted: {notCounted.map((e) => `${e.label} (${e.note?.split(":")[0].toLowerCase()})`).join(", ")}.</p>
              )}
            </section>
          </div>

          <div className="split split-even">
            <section className="card" aria-labelledby="daily-title">
              <h2 id="daily-title" className="card-title">Daily balances · next 30 days</h2>
              <div className="table-wrap daily-table">
                <table className="table">
                  <thead><tr><th>Date</th><th className="right">Expected</th><th className="right">Cautious</th></tr></thead>
                  <tbody>
                    {c.daily.map((d) => (
                      <tr key={d.date}>
                        <td className="nowrap">{dayMonth(d.date)}</td>
                        <td className={`num right ${c.reserve_minor !== null && d.expected_minor < c.reserve_minor ? "neg" : ""}`}>{money(d.expected_minor)}</td>
                        <td className={`num right ${c.reserve_minor !== null && d.cautious_minor < c.reserve_minor ? "neg" : ""}`}>{money(d.cautious_minor)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted small">Red figures are below the reserve of {c.reserve_minor !== null ? money(c.reserve_minor) : "—"}.</p>
            </section>
            <section className="card" aria-labelledby="acc-title">
              <h2 id="acc-title" className="card-title">By account</h2>
              <p className="muted small">Each account is projected on its own; cash in one account cannot cover another without a transfer.</p>
              <ul className="account-lines">
                {res.accounts.filter((a) => a.currency === ccy).map((a) => (
                  <li key={a.id}>
                    <span className="account-name"><Link className="link" to={`/records/accounts/${a.id}`}>{a.label}</Link>
                      <span className={`badge ${a.freshness === "fresh" ? "badge-success" : "badge-warning"} tiny-badge`}>{a.freshness === "fresh" ? "Fresh" : "Stale"}</span></span>
                    <span className="account-figs num">
                      <span>Lowest expected <strong>{money(a.lowest_expected_minor)}</strong></span>
                      <span>Lowest cautious <strong className={a.lowest_cautious_minor < 0 ? "neg" : ""}>{money(a.lowest_cautious_minor)}</strong></span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        </>
      )}

      <div className="split split-even">
        <section className="card" aria-labelledby="ev-title">
          <h2 id="ev-title" className="card-title">Stale or missing evidence</h2>
          {res.evidence.length === 0 && <p className="muted">Nothing missing: every account is fresh and every receipt is dated.</p>}
          <ul className="issue-list">
            {res.evidence.map((e, i) => (
              <li key={i}><span className={`badge ${e.severity === "warning" ? "badge-warning" : "badge-info"}`}>{e.code.replace(/_/g, " ")}</span><span>{e.message}</span></li>
            ))}
          </ul>
        </section>
        <section className="card" aria-labelledby="snap-title">
          <h2 id="snap-title" className="card-title">How this was calculated</h2>
          <dl className="lines">
            <div><dt>Calculated</dt><dd>{new Date(snap.created_at).toLocaleString("en-GB")} by {snap.created_by}</dd></div>
            <div><dt>As of</dt><dd>{fmtDate(snap.as_of)}</dd></div>
            <div><dt>Calculation version</dt><dd>{snap.calc_version}</dd></div>
            <div><dt>Frozen inputs</dt><dd><code title={snap.inputs_sha256}>{snap.inputs_sha256.slice(0, 16)}…</code></dd></div>
          </dl>
          <p className="muted small">Cautious path: receipts {res.rules.cautious_delay_days} days late, overdue receipts left out, stale balances not relied on. Expected path: overdue receipts assumed in {res.rules.overdue_assumed_days} days. Disputed receipts are never counted.</p>
          <div className="form-actions">
            <button type="button" className="btn btn-ghost btn-small" onClick={verify}>Verify figures</button>
            {canRun && snap.current.inputs_unchanged && <button type="button" className="btn btn-primary btn-small" disabled={busy} onClick={recalc}>{busy ? "Calculating…" : "Recalculate"}</button>}
          </div>
        </section>
      </div>
    </>
  );
}

function BreachBanner({ c, money }: { c: CurrencyResult; money: (m: number) => string }) {
  if (c.reserve_minor === null) return <p className="notice">No reserve is set for {c.currency}; paths are shown without a floor.</p>;
  const e = c.first_breach.expected, k = c.first_breach.cautious;
  const long = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "long" });
  if (e || k) {
    const b = (e ?? k)!;
    return (
      <section className="banner banner-warning" role="alert">
        <span className="banner-icon" aria-hidden="true">!</span>
        <div className="banner-body">
          <strong>Reserve shortfall projected for <span className="nowrap">{long(b.date)}</span>{e ? "" : " on the cautious path"}</strong>
          <span className="banner-line">
            <span className="nowrap">{e ? "Expected" : "Cautious"} balance {money(b.balance_minor)}</span> · <span className="nowrap">owner reserve {money(c.reserve_minor)}</span> · <span className="nowrap">gap {money(b.gap_minor)}</span>
          </span>
          {!e && <span className="banner-note">The cautious path assumes late receipts and does not rely on stale balances; the expected path stays above the reserve.</span>}
        </div>
      </section>
    );
  }
  return (
    <section className="banner banner-success" role="status">
      <span className="banner-icon" aria-hidden="true">✓</span>
      <div className="banner-body">
        <strong>No reserve shortfall in the next 13 weeks</strong>
        <span className="banner-line">
          <span className="nowrap">Lowest cautious balance {money(c.lowest.cautious.amount_minor)}</span> on <span className="nowrap">{fmtDate(c.lowest.cautious.date)}</span> · <span className="nowrap">owner reserve {money(c.reserve_minor)}</span>
        </span>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import { exponentOf } from "../settings/fields";
import { DestinationsCard } from "./DestinationsCard";
import { FlagPanel } from "./FlagPanel";
import { RULE_BADGE, STATE_BADGE, TXN_BADGE, type Flag, type FlagState, type Overview, type PayeeDestinations, type RulesInfo, type TxnRow } from "./types";

type Filter = "unresolved" | "confirmed" | "cleared" | "all";
const FILTERS: [Filter, string][] = [["unresolved", "Needs review"], ["confirmed", "Investigating"], ["cleared", "Cleared"], ["all", "All"]];
const shortDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" });

// Layout follows images/07-fraud-monitor.png: hold banner; Recent transactions on the left and "Why this was flagged" on the
// right. The flag list and payee destinations sit below. Styling follows design.md.
export function FraudMonitorPage() {
  const { active, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}` : null;
  const can = (a: string) => (active?.allowed_actions ?? []).includes(a);
  const canRead = can("fraud.read");
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));
  const [timezone, setTimezone] = useState<string | undefined>(undefined);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [txns, setTxns] = useState<TxnRow[] | null>(null);
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [rulesInfo, setRulesInfo] = useState<RulesInfo | null>(null);
  const [dests, setDests] = useState<PayeeDestinations | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("unresolved");
  const [panelKey, setPanelKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState<string | null>(null);
  const autoScannedFor = useRef<string | null>(null);
  const canRun = active?.allowed_actions.includes("fraud.run") ?? false;

  useEffect(() => {
    if (!base) return;
    fetch(base).then((r) => (r.ok ? r.json() : null)).then((b: { timezone?: string } | null) => setTimezone(b?.timezone));
  }, [base]);

  const load = useCallback(async () => {
    if (!base || !canRead) return;
    const [o, t, f, r, d] = await Promise.all([
      api<Overview>(`${base}/fraud/overview`), api<{ items: TxnRow[] }>(`${base}/fraud/transactions`), api<{ items: Flag[] }>(`${base}/fraud/flags`),
      api<RulesInfo>(`${base}/fraud/rules`), api<PayeeDestinations>(`${base}/fraud/payee-destinations`),
    ]);
    const failed = [o, t, f, r, d].find((x) => !x.ok);
    setError(failed && !failed.ok ? failed.message : null);
    if (o.ok) setOverview(o.data);
    if (t.ok) setTxns(t.data.items);
    if (r.ok) setRulesInfo(r.data);
    if (d.ok) setDests(d.data);
    if (f.ok) {
      setFlags(f.data.items);
      // Open the most urgent flag first: unresolved and holding a payment, as in the mockup.
      setSelected((cur) => cur ?? f.data.items.find((x) => x.state === "open" || x.state === "deferred")?.id ?? null);
    }
  }, [base, canRead]);
  useEffect(() => { setSelected(null); load(); }, [load]);

  const select = (id: string) => {
    setSelected(id);
    requestAnimationFrame(() => {
      if (window.matchMedia("(max-width: 1100px)").matches) document.getElementById("flag-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const scan = async () => {
    if (!base) return;
    setScanning(true);
    setScanNote(null);
    const r = await api<{ flags_created: number; flags_existing: number; holds_added: number; items_checked: number }>(`${base}/fraud/scans`, "POST", {});
    setScanning(false);
    if (!r.ok) return setScanNote(r.message);
    setScanNote(`Checked ${r.data.items_checked} records: ${r.data.flags_created} new flag(s), ${r.data.flags_existing} already known${r.data.holds_added ? `, ${r.data.holds_added} payment(s) put on hold` : ""}.`);
    setPanelKey((k) => k + 1);
    load();
  };

  useEffect(() => {
    const businessId = active?.business_id;
    if (!businessId || !canRun || autoScannedFor.current === businessId) return;
    autoScannedFor.current = businessId;
    void scan();
  }, [active?.business_id, canRun]);

  const changed = () => load();

  if (!active) return null;
  const held = overview?.held_payments ?? [];
  const firstHeld = held[0];
  const shown = (flags ?? []).filter((f) => filter === "all" || (filter === "unresolved" ? f.state === "open" || f.state === "deferred" : f.state === filter));
  const counts: Record<FlagState, number> = overview?.counts ?? { open: 0, deferred: 0, confirmed: 0, cleared: 0 };

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Transaction Fraud Monitor</h1>
          <p className="page-sub">Review suspicious activity before it affects a payment or funding decision</p>
        </div>
        {canRead && (
          <div className="header-actions">
            {can("fraud.run") && <button type="button" className="btn btn-primary" onClick={scan} disabled={scanning}>{scanning ? "Checking…" : "Run checks"}</button>}
          </div>
        )}
      </div>

      {!canRead && (
        <section className="card">
          <p className="badge badge-info block-badge" role="status">Only owners and bookkeepers can see the Fraud Monitor.</p>
        </section>
      )}
      {error && <p className="badge badge-danger block-badge" role="alert">{error}</p>}
      {scanNote && <p className="badge badge-info block-badge" role="status">{scanNote}</p>}

      {canRead && overview && (
        firstHeld ? (
          <div className="banner banner-danger" role="alert">
            <span className="banner-icon" aria-hidden="true">!</span>
            <div className="banner-body">
              <strong>{held.length} proposed payment{held.length > 1 ? "s" : ""} on hold</strong>
              <span className="banner-line">{firstHeld.flags[0]?.title ?? "Suspected activity"}: the payment to {firstHeld.payee} ({money(firstHeld.amount_minor, firstHeld.currency)}, {firstHeld.reference}) requires human review.</span>
            </div>
          </div>
        ) : overview.unresolved > 0 ? (
          <div className="banner banner-warning" role="status">
            <span className="banner-icon" aria-hidden="true">?</span>
            <div className="banner-body">
              <strong>{overview.unresolved === 1 ? "1 flag needs review" : `${overview.unresolved} flags need review`}</strong>
              <span className="banner-line">No proposed payment is on hold. A flag is suspected activity, not a finding of fraud.</span>
            </div>
          </div>
        ) : (
          <div className="banner banner-success" role="status">
            <span className="banner-icon" aria-hidden="true">✓</span>
            <div className="banner-body">
              <strong>No suspected activity needs review</strong>
            </div>
          </div>
        )
      )}

      {canRead && (
        <div className="fraud-grid">
          <section className="card" aria-labelledby="txn-title">
            <h2 id="txn-title" className="section-title">Recent transactions</h2>
            {!txns && <p className="muted">Loading…</p>}
            {txns && txns.length === 0 && <p className="muted">No outgoing payments in the last 30 days.</p>}
            {txns && txns.length > 0 && (
              <div className="table-wrap">
                <table className="table txn-table">
                  <thead><tr><th>Date</th><th>Payee</th><th className="right">Amount</th><th>Status</th></tr></thead>
                  <tbody>
                    {txns.map((t) => {
                      const [cls, label] = TXN_BADGE[t.status];
                      const flagId = t.flag_ids[0];
                      return (
                        <tr key={t.type + t.id} className={flagId && flagId === selected ? "is-selected" : undefined}>
                          <td className="nowrap muted">{shortDate(t.date)}</td>
                          <td className="txn-payee">
                            <strong>{t.payee ?? "—"}</strong>
                            {t.reference && <span className="muted small"> · {t.reference}</span>}
                          </td>
                          <td className="right num nowrap">{money(t.amount_minor, t.currency)}</td>
                          <td>
                            {flagId ? (
                              <button type="button" className={`badge ${cls} badge-button`} onClick={() => select(flagId)} aria-label={`${label}: show why ${t.payee ?? "this payment"} was flagged`}>{label}</button>
                            ) : <span className={`badge ${cls}`}>{label}</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <p className="muted small">Posted payments from the last 30 days and open sandbox payment proposals. Own-account transfers are left out.</p>
          </section>

          {base && selected ? (
            <FlagPanel key={selected + panelKey} base={base} flagId={selected} rulesInfo={rulesInfo} timezone={timezone} money={money} onChanged={changed} />
          ) : (
            <section className="card" aria-labelledby="flag-title">
              <h2 id="flag-title" className="section-title">Why this was flagged</h2>
              <p className="muted">{flags && flags.length ? "Choose a flag to see the compared records and the cash effect." : "No flags yet. Run checks to evaluate recent payments."}</p>
            </section>
          )}
        </div>
      )}

      {canRead && flags && (
        <section className="card" aria-labelledby="flags-title">
          <div className="card-head">
            <h2 id="flags-title" className="section-title">Flags</h2>
            <div className="tabs" role="tablist" aria-label="Filter flags">
              {FILTERS.map(([k, label]) => {
                const n = k === "unresolved" ? counts.open + counts.deferred : k === "all" ? flags.length : counts[k];
                return <button key={k} type="button" role="tab" aria-selected={filter === k} className={`tab ${filter === k ? "is-active" : ""}`} onClick={() => setFilter(k)}>{label} ({n})</button>;
              })}
            </div>
          </div>
          {shown.length === 0 && <p className="muted">Nothing here.</p>}
          <ul className="flag-list">
            {shown.map((f) => {
              const [cls, label] = STATE_BADGE[f.state];
              const who = f.evidence.subjects[0]?.payee;
              return (
                <li key={f.id} className={`flag-item${f.id === selected ? " is-selected" : ""}`}>
                  <span className={`rule-badge ${f.severity === "high" ? "rule-high" : "rule-medium"}`}>{RULE_BADGE[f.rule]}</span>
                  <div className="flag-body">
                    <span className="flag-title">{f.title}{who ? ` · ${who}` : ""}</span>
                    <span className="muted small">{f.amount_minor ? `${money(f.amount_minor, f.currency)} · ` : ""}found {fmtDate(f.created_at.slice(0, 10))}{f.holding ? ` · holding ${f.holds.length} payment(s)` : ""}</span>
                  </div>
                  <span className={`badge ${cls}`}>{label}</span>
                  <button type="button" className="btn btn-ghost btn-small" onClick={() => select(f.id)}>View</button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {canRead && base && (
        <DestinationsCard base={base} data={dests} canWrite={can("payees.destinations.write")}
                          onRecorded={(flagId) => { if (flagId) setSelected(flagId); setPanelKey((k) => k + 1); load(); }} />
      )}
    </>
  );
}

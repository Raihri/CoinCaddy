import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { RegionNote } from "../region/RegionNote";
import { Link, useNavigate } from "react-router-dom";
import { fmtDate } from "../api";
import { useSession } from "../auth/session";
import { ACCOUNT_STATUS, ACCOUNT_STATUS_HELP, type Position } from "../cash/types";
import { formatMoney } from "../money";
import { readErrors } from "../settings/apiErrors";
import { exponentOf, Field, KIND_LABELS } from "../settings/fields";
import type { ImportBatch } from "./types";

type Summary = {
  accounts: { id: string; label: string; kind: string; currency: string; posted_transactions: number;
              latest_business_date: string | null; last_posted_at: string | null }[];
  open_imports: { id: string; source_name: string; status: ImportBatch["status"]; account_label: string; row_count: number;
                  error_rows: number; pending_rows: number; included_rows: number; uploaded_by: string; created_at: string }[];
  notice: string;
};
type LedgerTxn = { id: string; business_date: string; amount_minor: number; currency: string; description: string;
                   reference: string | null; account_label: string; source_row_number: number };

const MONOGRAM: Record<string, string> = { bank: "BK", mobile_wallet: "WL", cash: "CA" };

type ReviewItem = { key: string; to: string; title: string; detail: string; tags: [string, string][] };

export function RecordsPage() {
  const { active, profile, options } = useSession();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [pos, setPos] = useState<Position | null>(null);
  const [ledger, setLedger] = useState<{ total: number; transactions: LedgerTxn[] } | null>(null);
  const base = active ? `/api/businesses/${active.business_id}` : null;

  const load = useCallback(() => {
    if (!base) return;
    fetch(`${base}/records/summary`).then((r) => (r.ok ? r.json() : null)).then(setSummary);
    fetch(`${base}/cash/position`).then((r) => (r.ok ? r.json() : null)).then(setPos);
    fetch(`${base}/ledger?limit=20`).then((r) => (r.ok ? r.json() : null)).then(setLedger);
  }, [base]);
  useEffect(load, [load]);

  if (!active) return null;
  const money = (m: number | null, c: string) => (m === null ? "Unknown" : formatMoney(m, c, exponentOf(options, c)));

  // One review queue: staged imports, account reconciliation issues, and transfer problems.
  const items: ReviewItem[] = [
    ...(summary?.open_imports ?? []).map((i) => ({
      key: i.id, to: `/records/imports/${i.id}`, title: i.source_name,
      detail: `${i.account_label} · ${i.row_count} rows · uploaded by ${i.uploaded_by}`,
      tags: [
        ...(i.status === "needs_mapping" ? [["badge-warning", "Map columns"] as [string, string]] : []),
        ...(i.error_rows > 0 ? [["badge-danger", `${i.error_rows} rejected`] as [string, string]] : []),
        ...(i.pending_rows > 0 ? [["badge-warning", `${i.pending_rows} possible duplicate${i.pending_rows > 1 ? "s" : ""}`] as [string, string]] : []),
      ],
    })),
    ...(pos?.accounts ?? []).filter((a) => a.issues.length > 0).map((a) => ({
      key: a.account.id, to: `/records/accounts/${a.account.id}`, title: a.account.label,
      detail: a.issues[0].message, tags: a.issues.map((i) => ["badge-warning", i.code.replace(/_/g, " ")] as [string, string]),
    })),
    ...((pos?.transfer_suggestions.length ?? 0) > 0
      ? [{ key: "sugg", to: "/records/transfers", title: "Possible own-account transfers",
           detail: `${pos!.transfer_suggestions.length} pair(s) look like money moved between your accounts.`, tags: [["badge-info", "Confirm pairs"] as [string, string]] }]
      : []),
  ];

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Records &amp; reconciliation</h1>
          <p className="page-sub">Review sources and confirm what can be used in calculations</p>
        </div>
        <div className="header-actions">
          <Link to="/records/obligations" className="btn btn-shortcut">Invoices &amp; Bills</Link>
          <Link to="/records/transfers" className="btn btn-shortcut">Transfers</Link>
        </div>
      </div>
      <RegionNote />

      <section className="card records-guide" aria-labelledby="records-guide-title">
        <div className="card-head"><h2 id="records-guide-title" className="card-title">From statement to usable cash</h2><span className="badge badge-info">3 steps</span></div>
        <ol className="records-steps">
          <li><span className="step-number">1</span><span><strong>Import</strong><small>Upload a bank or wallet statement. Invoices and bills are added separately.</small></span></li>
          <li><span className="step-number">2</span><span><strong>Review and post</strong><small>Resolve duplicates, check rejected rows, then post the rows you accept.</small></span></li>
          <li><span className="step-number">3</span><span><strong>Verify</strong><small>Check balance evidence and statement coverage. Posting alone does not reconcile an account.</small></span></li>
        </ol>
      </section>

      <UploadCard accounts={profile?.accounts ?? []} />

      {pos && pos.totals.length > 0 && (
        <section className="card" aria-labelledby="position-title">
          <div className="card-head"><h2 id="position-title" className="card-title">Spendable now</h2><span className="badge badge-info">By currency</span></div>
          <p className="muted small spendable-help">This total uses accounts with recent balance evidence, subtracts restricted funds, and excludes stale or missing balances. Open an account below to see the source and calculation.</p>
          <div className="currency-grid">
            {pos.totals.map((t) => (
              <div key={t.currency} className="currency-total">
                <span className="kpi-label">{t.currency}</span>
                <span className="kpi-value num">{money(t.spendable_minor, t.currency)}</span>
                <span className="muted small">
                  From {t.included.length} verified account{t.included.length === 1 ? "" : "s"}, after {money(t.restricted_minor, t.currency)} restricted.
                </span>
                {(t.stale.length > 0 || t.missing.length > 0) && (
                  <div className="excluded small"><strong>Not included:</strong>
                    {[...t.stale.map((account) => ({ ...account, reason: "stale" })), ...t.missing.map((account) => ({ ...account, reason: "no balance" }))]
                      .map((account) => <Link key={account.account_id} to={`/records/accounts/${account.account_id}`} className="excluded-link">{account.label} ({account.reason})</Link>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="split split-even">
        <section className="card" aria-labelledby="connected-title">
          <h2 id="connected-title" className="card-title">Accounts and balances</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Source</th><th>Last verified</th><th className="right">Usable</th><th>Status</th></tr></thead>
              <tbody>
                {(pos?.accounts ?? []).map((a) => {
                  const posted = summary?.accounts.find((s) => s.id === a.account.id)?.posted_transactions ?? 0;
                  const [cls, label] = ACCOUNT_STATUS[a.status];
                  return (
                    <tr key={a.account.id}>
                      <td>
                        <Link to={`/records/accounts/${a.account.id}`} className="source source-link">
                          <span className="nav-mono" aria-hidden="true">{MONOGRAM[a.account.kind] ?? "AC"}</span>
                          <span><strong>{a.account.label}</strong>
                            <span className="muted small block">{KIND_LABELS[a.account.kind]} · {a.account.currency} · {posted} posted</span></span>
                        </Link>
                      </td>
                      <td className="nowrap">{a.evidence_through ? fmtDate(a.evidence_through) : <span className="muted">Not counted</span>}</td>
                      <td className="num right">{money(a.usable_minor, a.account.currency)}</td>
                      <td><span className={`badge ${cls}`} title={ACCOUNT_STATUS_HELP[a.status]}>{label}</span>
                        <span className="muted small block status-help">{ACCOUNT_STATUS_HELP[a.status]}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card" aria-labelledby="review-title">
          <div className="card-head">
            <h2 id="review-title" className="card-title">Items to review</h2>
            {summary && pos && <span className={`badge ${items.length ? "badge-warning" : "badge-success"}`}>
              {items.length ? `${items.length} unresolved` : "Nothing open"}</span>}
          </div>
          {summary && pos && items.length === 0 && <p className="muted">Nothing needs review.</p>}
          <ol className="review-list">
            {items.map((i, n) => (
              <li key={i.key}>
                <Link to={i.to} className="review-item">
                  <span className="review-num">{String(n + 1).padStart(2, "0")}</span>
                  <span>
                    <strong>{i.title}</strong>
                    <span className="muted small block">{i.detail}</span>
                    <span className="review-tags">{i.tags.map(([c, t], k) => <span key={k} className={`badge ${c}`}>{t}</span>)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <section className="card" aria-labelledby="ledger-title">
        <h2 id="ledger-title" className="card-title">Posted transactions</h2>
        {ledger && ledger.total > 20 && <p className="muted small">Latest 20 of {ledger.total}.</p>}
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Reference</th><th className="right">Amount</th></tr></thead>
            <tbody>
              {ledger?.transactions.length === 0 && <tr><td colSpan={5} className="muted">Nothing posted yet.</td></tr>}
              {ledger?.transactions.map((t) => (
                <tr key={t.id}>
                  <td>{fmtDate(t.business_date)}</td>
                  <td>{t.account_label}</td>
                  <td>{t.description}</td>
                  <td className="muted">{t.reference ?? "—"}</td>
                  <td className={`num right ${t.amount_minor < 0 ? "neg" : "pos"}`}>
                    {formatMoney(t.amount_minor, t.currency, exponentOf(options, t.currency))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function UploadCard({ accounts }: { accounts: { id: string; label: string; currency: string }[] }) {
  const { active } = useSession();
  const navigate = useNavigate();
  const [accountId, setAccountId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const canStage = active?.allowed_actions.includes("import.stage") ?? false;

  useEffect(() => { if (!accountId && accounts[0]) setAccountId(accounts[0].id); }, [accounts, accountId]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const file = fileRef.current?.files?.[0];
    if (!file) return setError("Choose a .csv or .xlsx file.");
    if (!/\.(csv|xlsx)$/i.test(file.name)) return setError("Only .csv and .xlsx files are supported.");
    if (file.size > 2 * 1024 * 1024) return setError("The file is larger than 2 MB.");
    const form = new FormData();
    form.append("account_id", accountId);
    form.append("file", file);
    setBusy(true);
    const r = await fetch(`/api/businesses/${active!.business_id}/imports`, { method: "POST", body: form });
    setBusy(false);
    if (!r.ok) return setError((await readErrors(r))._form ?? "Upload failed.");
    const batch = (await r.json()) as ImportBatch;
    navigate(`/records/imports/${batch.id}`, { state: { duplicateUpload: batch.duplicate_upload } });
  };

  return (
    <section className="card upload-card" aria-labelledby="upload-title">
      <div className="upload-intro">
        <span className="nav-mono" aria-hidden="true">UP</span>
        <div>
          <h2 id="upload-title" className="card-title">Import new records</h2>
          <p className="muted small">Upload a bank or wallet statement (CSV/XLSX). Add customer invoices and supplier bills from Invoices &amp; Bills.</p>
        </div>
      </div>
      {canStage ? (
        <form className="upload-form" onSubmit={submit} noValidate>
          <Field label="Account">
            <select className="select" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.label} ({a.currency})</option>)}
            </select>
          </Field>
          <Field label="Statement file (CSV/XLSX)">
            <input ref={fileRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
          </Field>
          <button type="submit" className="btn btn-primary" disabled={busy || !accountId}>{busy ? "Uploading…" : "Upload statement"}</button>
          {error && <p className="badge badge-danger span-all" role="alert">{error}</p>}
        </form>
      ) : <p className="muted">Your role cannot upload records.</p>}
    </section>
  );
}

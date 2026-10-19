import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useSession } from "../auth/session";
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

export function RecordsPage() {
  const { active, profile, options } = useSession();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [ledger, setLedger] = useState<{ total: number; transactions: LedgerTxn[] } | null>(null);
  const base = active ? `/api/businesses/${active.business_id}` : null;

  const load = useCallback(() => {
    if (!base) return;
    fetch(`${base}/records/summary`).then((r) => (r.ok ? r.json() : null)).then(setSummary);
    fetch(`${base}/ledger?limit=20`).then((r) => (r.ok ? r.json() : null)).then(setLedger);
  }, [base]);
  useEffect(load, [load]);

  if (!active) return null;
  const fmtDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Records</h1>
          <p className="page-sub">Review sources and confirm what can be used in calculations</p>
        </div>
      </div>

      <UploadCard accounts={profile?.accounts ?? []} />

      <div className="split split-even">
        <section className="card" aria-labelledby="connected-title">
          <h2 id="connected-title" className="card-title">Connected records</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Source</th><th>Posted</th><th>Latest record</th><th>Status</th></tr></thead>
              <tbody>
                {summary?.accounts.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <span className="source">
                        <span className="nav-mono" aria-hidden="true">{MONOGRAM[a.kind] ?? "AC"}</span>
                        <span><strong>{a.label}</strong><span className="muted small block">{KIND_LABELS[a.kind]} · {a.currency}</span></span>
                      </span>
                    </td>
                    <td className="num">{a.posted_transactions}</td>
                    <td>{a.latest_business_date ? fmtDate(a.latest_business_date) : <span className="muted">—</span>}</td>
                    <td>
                      {a.posted_transactions
                        ? <span className="badge badge-info" title="Balances are reconciled in a later step">Imported · not reconciled</span>
                        : <span className="badge badge-muted">No records</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Reconciliation against statement balances is not built yet (Prompt 05). Counts include posted rows only.</p>
        </section>

        <section className="card" aria-labelledby="review-title">
          <div className="card-head">
            <h2 id="review-title" className="card-title">Items to review</h2>
            {summary && <span className={`badge ${summary.open_imports.length ? "badge-warning" : "badge-success"}`}>
              {summary.open_imports.length ? `${summary.open_imports.length} open` : "Nothing open"}</span>}
          </div>
          {summary?.open_imports.length === 0 && <p className="muted">No staged imports are waiting.</p>}
          <ol className="review-list">
            {summary?.open_imports.map((i, n) => (
              <li key={i.id}>
                <Link to={`/records/imports/${i.id}`} className="review-item">
                  <span className="review-num">{String(n + 1).padStart(2, "0")}</span>
                  <span>
                    <strong>{i.source_name}</strong>
                    <span className="muted small block">
                      {i.account_label} · {i.row_count} rows · uploaded by {i.uploaded_by}
                    </span>
                    <span className="review-tags">
                      {i.status === "needs_mapping" && <span className="badge badge-warning">Map columns</span>}
                      {i.error_rows > 0 && <span className="badge badge-danger">{i.error_rows} rejected</span>}
                      {i.pending_rows > 0 && <span className="badge badge-warning">{i.pending_rows} possible duplicate{i.pending_rows > 1 ? "s" : ""}</span>}
                      {i.status === "staged" && <span className="badge badge-info">{i.included_rows} selected</span>}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <section className="card" aria-labelledby="ledger-title">
        <h2 id="ledger-title" className="card-title">Posted transactions</h2>
        <p className="muted small">Only posted rows appear here. {ledger ? `${ledger.total} in total; latest 20 shown.` : ""}</p>
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
  const canPost = active?.allowed_actions.includes("import.commit") ?? false;

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
          <p className="muted small">
            Upload a bank or wallet statement as .csv (UTF-8) or .xlsx: up to 2 MB, 5,000 rows, 30 columns, one sheet,
            no formulas or macros. Rows are staged for review and are not official until posted.
            {!canPost && " An owner or bookkeeper must review and post them."}
          </p>
        </div>
      </div>
      {canStage ? (
        <form className="upload-form" onSubmit={submit} noValidate>
          <Field label="Account">
            <select className="select" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.label} ({a.currency})</option>)}
            </select>
          </Field>
          <Field label="Statement file">
            <input ref={fileRef} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" />
          </Field>
          <button type="submit" className="btn btn-primary" disabled={busy || !accountId}>{busy ? "Uploading…" : "Upload records"}</button>
          {error && <p className="badge badge-danger span-all" role="alert">{error}</p>}
        </form>
      ) : <p className="muted">Your role cannot upload records.</p>}
    </section>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import { readErrors } from "../settings/apiErrors";
import { exponentOf, Field } from "../settings/fields";
import { STATUS_BADGE, type ImportBatch, type Mapping, type StagedRow } from "./types";

const VIEWS = [
  ["all", "All rows"], ["errors", "Rejected"], ["duplicates", "Possible duplicates"], ["included", "Selected"], ["excluded", "Excluded"],
] as const;
type View = (typeof VIEWS)[number][0];

export function ImportDetailPage() {
  const { batchId } = useParams();
  const { active, options } = useSession();
  const location = useLocation();
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [rows, setRows] = useState<{ total: number; rows: StagedRow[] } | null>(null);
  const [view, setView] = useState<View>("all");
  const [message, setMessage] = useState<{ kind: "success" | "danger" | "warning"; text: string } | null>(
    (location.state as { duplicateUpload?: boolean } | null)?.duplicateUpload
      ? { kind: "warning", text: "This exact file was already uploaded for this account, so the existing import is shown. Nothing was staged twice." }
      : null,
  );
  const [busy, setBusy] = useState(false);
  const [editMapping, setEditMapping] = useState(false);
  const base = active ? `/api/businesses/${active.business_id}/imports/${batchId}` : null;
  const can = (a: string) => active?.allowed_actions.includes(a) ?? false;
  const exp = exponentOf(options, batch?.account.currency ?? "BDT");

  const loadRows = useCallback(async (v: View) => {
    if (!base) return;
    const r = await fetch(`${base}/rows?view=${v}&limit=500`);
    if (r.ok) setRows(await r.json());
  }, [base]);
  const load = useCallback(async () => {
    if (!base) return;
    const r = await fetch(base);
    if (!r.ok) return setMessage({ kind: "danger", text: (await readErrors(r))._form ?? "Import not found." });
    setBatch(await r.json());
  }, [base]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (batch && batch.status !== "needs_mapping") loadRows(view); }, [batch?.revision, view]); // eslint-disable-line react-hooks/exhaustive-deps

  // One idempotency key per previewed revision: a retried click reuses it, so the server posts at most once.
  const commitKey = useMemo(() => crypto.randomUUID(), [batch?.revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (path: string, method: string, body: object, onOk?: (b: ImportBatch) => void) => {
    setBusy(true);
    setMessage(null);
    const r = await fetch(`${base}${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    if (!r.ok) {
      const errs = await readErrors(r);
      setMessage({ kind: "danger", text: errs._form ?? Object.values(errs)[0] ?? "The request failed." });
      if (r.status === 409) load();
      return;
    }
    const b = (await r.json()) as ImportBatch;
    setBatch(b);
    onOk?.(b);
  };

  if (!batch) return message ? <p className={`badge badge-${message.kind}`}>{message.text}</p> : <p className="muted">Loading…</p>;
  const [badgeCls, badgeText] = STATUS_BADGE[batch.status];
  const editable = batch.status === "needs_mapping" || batch.status === "staged";

  return (
    <>
      <div className="page-header">
        <div>
          <p className="muted small"><Link className="link" to="/records">Records</Link> / Import</p>
          <h1 className="page-title">{batch.source_name}</h1>
          <p className="page-sub">
            {batch.account.label} · {batch.file_type.toUpperCase()} · {batch.row_count} rows · uploaded by {batch.uploaded_by}
            <span className={`badge ${badgeCls} inline-badge`}>{badgeText}</span>
          </p>
        </div>
      </div>

      {message && <p className={`badge badge-${message.kind}`} role="status">{message.text}</p>}
      {batch.status !== "posted" && <p className="notice">{batch.notice}</p>}
      {batch.status === "posted" && (
        <p className="badge badge-success">Posted {batch.posted_count} rows to the ledger on {new Date(batch.posted_at!).toLocaleString("en-GB")}.</p>
      )}

      {editable && can("import.stage") && (batch.status === "needs_mapping" || editMapping) && (
        <MappingCard batch={batch} busy={busy}
                     onApply={(m) => act("/mapping", "PUT", { expected_revision: batch.revision, mapping: m }, () => {
                       setEditMapping(false);
                       setMessage({ kind: "success", text: "Mapping applied. Review the preview below." });
                     })} />
      )}
      {batch.mapping && batch.status !== "needs_mapping" && !editMapping && (
        <section className="card mapping-summary">
          <span>
            <strong>Mapping</strong>{" "}
            <span className="muted">
              Date: {batch.headers[batch.mapping.date_column!]} ({batch.mapping.date_format}) · Description: {batch.headers[batch.mapping.description_column!]} ·{" "}
              {batch.mapping.amount_mode === "signed"
                ? `Amount: ${batch.headers[batch.mapping.amount_column!]}`
                : `Debit: ${batch.headers[batch.mapping.debit_column!]}, Credit: ${batch.headers[batch.mapping.credit_column!]}`}
            </span>
          </span>
          {batch.status === "staged" && can("import.stage") && (
            <button type="button" className="btn btn-ghost btn-small" onClick={() => setEditMapping(true)}>Change mapping</button>
          )}
        </section>
      )}

      {batch.status !== "needs_mapping" && (
        <section className="card" aria-labelledby="preview-title">
          <div className="card-head">
            <h2 id="preview-title" className="card-title">Preview</h2>
            <span className="muted small">
              {batch.counts.valid} valid · {batch.counts.error} rejected · {batch.counts.duplicates} possible duplicates
            </span>
          </div>
          <div className="tabs" role="tablist">
            {VIEWS.map(([v, label]) => (
              <button key={v} type="button" role="tab" aria-selected={view === v} className={`tab ${view === v ? "is-active" : ""}`}
                      onClick={() => setView(v)}>{label}</button>
            ))}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Row</th><th>Date</th><th>Description</th><th className="right">Amount</th><th>Check</th><th>Post?</th></tr></thead>
              <tbody>
                {rows?.rows.length === 0 && <tr><td colSpan={6} className="muted">No rows in this view.</td></tr>}
                {rows?.rows.map((r) => (
                  <RowLine key={r.row_number} row={r} exp={exp} currency={batch.account.currency}
                           canReview={can("import.review") && batch.status === "staged"} busy={busy}
                           onDecide={(d) => act(`/rows/${r.row_number}`, "PATCH", { expected_revision: batch.revision, decision: d })} />
                ))}
              </tbody>
            </table>
          </div>
          {rows && rows.total > rows.rows.length && <p className="muted small">Showing the first {rows.rows.length} of {rows.total}.</p>}

          {batch.status === "staged" && (
            <div className="commit-bar">
              <div>
                <strong>{batch.counts.included} rows selected to post</strong>
                {batch.counts.pending > 0 && <span className="field-error block">Review {batch.counts.pending} possible duplicate{batch.counts.pending > 1 ? "s" : ""} first.</span>}
                {!can("import.commit") && <span className="muted small block">Only an owner or bookkeeper can post.</span>}
              </div>
              <div className="form-actions">
                {can("import.review") && (
                  <button type="button" className="btn btn-ghost" disabled={busy}
                          onClick={() => act("/discard", "POST", { expected_revision: batch.revision },
                                             () => setMessage({ kind: "warning", text: "Import discarded. Nothing was posted." }))}>
                    Discard import
                  </button>
                )}
                {can("import.commit") && (
                  <button type="button" className="btn btn-primary"
                          disabled={busy || batch.counts.pending > 0 || batch.counts.included === 0}
                          onClick={() => act("/commit", "POST", { expected_revision: batch.revision, idempotency_key: commitKey },
                                             (b) => setMessage({ kind: "success", text: `Posted ${b.commit?.posted_count} rows to the ledger.` }))}>
                    {busy ? "Posting…" : `Post ${batch.counts.included} rows`}
                  </button>
                )}
              </div>
            </div>
          )}
        </section>
      )}
    </>
  );
}

function RowLine({ row, exp, currency, canReview, busy, onDecide }: {
  row: StagedRow; exp: number; currency: string; canReview: boolean; busy: boolean; onDecide: (d: "include" | "exclude") => void;
}) {
  const raw = (i: number) => row.raw[i]?.v ?? "";
  return (
    <tr className={row.status === "error" ? "row-error" : row.decision === "pending" ? "row-pending" : ""}>
      <td className="num">{row.row_number}</td>
      <td className="nowrap">{row.business_date ?? <span className="muted">{raw(0) || "—"}</span>}</td>
      <td className="wrap">{row.description ?? <span className="muted">{row.raw.filter(Boolean).map((c) => c!.v).join(" · ").slice(0, 80)}</span>}
        {row.reference && <span className="muted small block">Ref {row.reference}</span>}</td>
      <td className={`num right ${row.amount_minor !== null && row.amount_minor < 0 ? "neg" : "pos"}`}>
        {row.amount_minor !== null ? formatMoney(row.amount_minor, currency, exp) : "—"}
      </td>
      <td className="wrap">
        {row.status === "error" && row.errors.map((e, i) => (
          <span key={i} className="field-error block">{e.field}: {e.message}</span>
        ))}
        {row.duplicate?.kind === "in_file" && <span className="badge badge-warning">Same as row {row.duplicate.row_number}</span>}
        {row.duplicate?.kind === "in_ledger" && row.duplicate.transaction && (
          <span className="badge badge-warning">
            Matches posted {row.duplicate.transaction.business_date} · {formatMoney(row.duplicate.transaction.amount_minor, currency, exp)}
          </span>
        )}
        {row.status === "valid" && !row.duplicate && <span className="badge badge-success">Valid</span>}
      </td>
      <td>
        {row.status === "error" ? <span className="muted">Rejected</span>
          : row.decision === "pending" ? (
            canReview ? (
              <span className="decide">
                <button type="button" className="btn btn-small btn-ghost" disabled={busy} onClick={() => onDecide("exclude")}>Exclude</button>
                <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => onDecide("include")}>Keep both</button>
              </span>
            ) : <span className="badge badge-warning">Needs review</span>
          ) : canReview ? (
            <label className="check">
              <input type="checkbox" checked={row.decision === "include"} disabled={busy}
                     onChange={(e) => onDecide(e.target.checked ? "include" : "exclude")} />
              {row.decision === "include" ? "Post" : "Skip"}
            </label>
          ) : <span>{row.decision === "include" ? "Post" : "Skip"}</span>}
      </td>
    </tr>
  );
}

const FIELDS: [keyof Mapping, string, boolean][] = [
  ["date_column", "Date", true], ["description_column", "Description", true], ["reference_column", "Reference", false],
  ["counterparty_column", "Counterparty", false], ["currency_column", "Currency", false],
];

function MappingCard({ batch, busy, onApply }: { batch: ImportBatch; busy: boolean; onApply: (m: Mapping) => void }) {
  const [m, setM] = useState<Mapping>(batch.mapping ?? batch.suggested_mapping);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setM(batch.mapping ?? batch.suggested_mapping), [batch.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const formats = m.date_column !== null ? batch.column_profile[m.date_column]?.date_formats ?? [] : [];
  const columnSelect = (key: keyof Mapping, required: boolean) => (
    <select className="select" value={(m[key] as number | null) ?? ""}
            onChange={(e) => setM({ ...m, [key]: e.target.value === "" ? null : Number(e.target.value) })}>
      <option value="">{required ? "Choose a column" : "Not in file"}</option>
      {batch.headers.map((h, i) => (
        <option key={i} value={i}>{h}{batch.column_profile[i]?.sample ? ` (e.g. ${batch.column_profile[i].sample})` : ""}</option>
      ))}
    </select>
  );

  const apply = () => {
    setError(null);
    if (m.date_column === null || m.description_column === null) return setError("Choose the date and description columns.");
    if (!m.date_format) return setError("Choose the date format used in the file.");
    if (m.amount_mode === "signed" ? m.amount_column === null : m.debit_column === null || m.credit_column === null) {
      return setError("Choose the amount column(s).");
    }
    // Send exactly the mapping fields; the server rejects anything extra (e.g. display-only hints).
    const clean: Mapping = {
      date_column: m.date_column, date_format: m.date_format, description_column: m.description_column,
      amount_mode: m.amount_mode,
      amount_column: m.amount_mode === "signed" ? m.amount_column : null,
      debit_column: m.amount_mode === "debit_credit" ? m.debit_column : null,
      credit_column: m.amount_mode === "debit_credit" ? m.credit_column : null,
      counterparty_column: m.counterparty_column, reference_column: m.reference_column, currency_column: m.currency_column,
    };
    onApply(clean);
  };

  return (
    <section className="card" aria-labelledby="map-title">
      <h2 id="map-title" className="card-title">Field mapping</h2>
      <p className="muted small">Tell us which column holds each field. Amounts use “.” for decimals; commas only as thousands or lakh separators.</p>
      <div className="form-grid">
        {FIELDS.map(([key, label, required]) => (
          <Field key={key} label={`${label}${required ? "" : " (optional)"}`}>{columnSelect(key, required)}</Field>
        ))}
        <Field label="Date format"
               hint={formats.length > 1 ? undefined : formats.length === 1 ? "Detected from the file." : undefined}
               error={formats.length > 1 && !m.date_format ? `Ambiguous: dates fit ${formats.join(" and ")}. Choose the one your bank uses.` : undefined}>
          <select className="select" value={m.date_format ?? ""} onChange={(e) => setM({ ...m, date_format: e.target.value || null })}>
            <option value="">Choose a format</option>
            {["YYYY-MM-DD", "YYYY/MM/DD", "DD/MM/YYYY", "MM/DD/YYYY", "DD-MM-YYYY", "DD.MM.YYYY"].map((f) => (
              <option key={f} value={f}>{f}{formats.includes(f) ? " — fits this file" : ""}</option>
            ))}
          </select>
        </Field>
        <Field label="Amounts are in">
          <select className="select" value={m.amount_mode} onChange={(e) => setM({ ...m, amount_mode: e.target.value as Mapping["amount_mode"] })}>
            <option value="signed">One signed column (negative = money out)</option>
            <option value="debit_credit">Separate debit and credit columns</option>
          </select>
        </Field>
        {m.amount_mode === "signed"
          ? <Field label="Amount">{columnSelect("amount_column", true)}</Field>
          : <>
              <Field label="Debit (money out)">{columnSelect("debit_column", true)}</Field>
              <Field label="Credit (money in)">{columnSelect("credit_column", true)}</Field>
            </>}
      </div>
      {error && <p className="badge badge-danger" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={apply}>
          {batch.mapping ? "Re-apply mapping" : "Apply mapping and preview"}
        </button>
      </div>
    </section>
  );
}

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, fmtDate } from "../api";
import { VoidButton } from "../cash/AccountPage";
import type { FieldErrors } from "../settings/apiErrors";
import { errorFor } from "../settings/apiErrors";
import { Field } from "../settings/fields";

// Document register used by Financing Readiness. It records only that a document is held and where; never the
// document or its number (the server refuses 8+ digits in a row).
type Doc = { id: string; kind: string; reference: string; issued_on: string | null; expires_on: string | null; created_by: string; created_at: string;
             voided: boolean; void_reason: string | null };

export const REGISTER_LABEL: Record<string, string> = { id: "Owner ID", trade_licence: "Trade licence", tax_certificate: "Tax certificate",
                                                        guarantor_letter: "Guarantor letter", collateral_valuation: "Collateral valuation" };

export function DocumentsCard({ base, canWrite, onChanged }: { base: string; canWrite: boolean; onChanged: () => void }) {
  const url = `${base}/business-documents`;
  const [docs, setDocs] = useState<Doc[]>([]);
  const [f, setF] = useState({ kind: "id", reference: "", issued_on: "", expires_on: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => { const r = await api<Doc[]>(url); if (r.ok) setDocs(r.data); }, [url]);
  useEffect(() => { load(); }, [load]);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.reference.trim()) return setErrors({ reference: "Say where the document is kept." });
    if (/\d{8}/.test(f.reference.replace(/[\s-]/g, ""))) return setErrors({ reference: "Do not enter ID, licence, or account numbers. Say where the document is kept instead." });
    if (f.issued_on && f.expires_on && f.expires_on <= f.issued_on) return setErrors({ expires_on: "The expiry date must be after the issue date." });
    setBusy(true);
    const r = await api<Doc>(url, "POST", { kind: f.kind, reference: f.reference, issued_on: f.issued_on || null, expires_on: f.expires_on || null });
    setBusy(false);
    if (!r.ok) return setErrors({ _form: r.message, ...r.errors });
    setErrors({});
    setF({ kind: f.kind, reference: "", issued_on: "", expires_on: "" });
    load();
    onChanged();
  };

  return (
    <section className="card" aria-labelledby="docs-title">
      <h2 id="docs-title" className="card-title">Documents for financing</h2>
      <p className="muted small">Record that you hold a document and where it is kept. Do not enter ID, licence, or account numbers. Nothing here is shared with any lender.</p>
      {docs.length === 0 && <p className="muted">No documents recorded yet.</p>}
      {docs.length > 0 && (
        <div className="table-wrap"><table className="table"><thead><tr><th>Document</th><th>Where it is kept</th><th>Issued</th><th>Expires</th><th>Recorded</th><th /></tr></thead><tbody>
          {docs.map((d) => (
            <tr key={d.id} className={d.voided ? "muted" : ""}>
              <td className="nowrap">{REGISTER_LABEL[d.kind] ?? d.kind}</td>
              <td className="wrap">{d.reference}{d.voided && <span className="badge badge-muted inline-badge">Voided: {d.void_reason}</span>}</td>
              <td className="nowrap">{fmtDate(d.issued_on)}</td>
              <td className="nowrap">{fmtDate(d.expires_on)}</td>
              <td className="nowrap">{d.created_by}, {new Date(d.created_at).toLocaleDateString("en-GB")}</td>
              <td>{canWrite && !d.voided && <VoidButton onVoid={async (reason) => {
                const r = await api(`${url}/${d.id}/void`, "POST", { reason });
                if (!r.ok) return r.message;
                load(); onChanged(); return null;
              }} />}</td>
            </tr>
          ))}
        </tbody></table></div>
      )}
      {canWrite ? (
        <form className="form-grid form-grid-4 stack-form" onSubmit={add} noValidate aria-label="Record a document">
          {errors._form && <p className="badge badge-danger span-all" role="alert">{errors._form}</p>}
          <Field label="Document" error={errors.kind}>
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{Object.entries(REGISTER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          </Field>
          <Field label="Where it is kept" error={errorFor(errors, "reference")} hint="For example: scanned copy, office drive.">
            <input value={f.reference} maxLength={80} onChange={(e) => setF({ ...f, reference: e.target.value })} />
          </Field>
          <Field label="Issued on (optional)" error={errorFor(errors, "issued_on")}><input type="date" value={f.issued_on} onChange={(e) => setF({ ...f, issued_on: e.target.value })} /></Field>
          <Field label="Expires on (optional)" error={errorFor(errors, "expires_on")}><input type="date" value={f.expires_on} onChange={(e) => setF({ ...f, expires_on: e.target.value })} /></Field>
          <div className="form-actions span-all"><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Recording…" : "Record document"}</button></div>
        </form>
      ) : <p className="muted small">Only the owner can record or void documents.</p>}
    </section>
  );
}

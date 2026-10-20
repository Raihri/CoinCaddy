import { useState, type FormEvent } from "react";
import { api, fmtDate } from "../api";
import { textProblem } from "../approvals/types";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { Field } from "../settings/fields";
import type { PayeeDestinations } from "./types";

type Recorded = { party: string; institution: string; masked: string; is_change: boolean; flag_id: string | null; scan: { holds_added: number } };

// Where each supplier is paid. Only the last four digits are kept; a different destination from the one recorded before
// is a "destination changed" flag that holds payments to that supplier until someone else verifies it independently.
export function DestinationsCard({ base, data, canWrite, onRecorded }: {
  base: string; data: PayeeDestinations | null; canWrite: boolean; onRecorded: (flagId: string | null) => void;
}) {
  const blank = { party_id: "", kind: "bank", institution: "", account_number: "", change_source: "", note: "" };
  const [f, setF] = useState(blank);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Recorded | null>(null);
  const set = (k: keyof typeof f, v: string) => { setF((x) => ({ ...x, [k]: v })); setErrors((e) => ({ ...e, [k]: "", _form: "" })); };
  const current = data?.parties.find((p) => p.party_id === f.party_id)?.current ?? null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: FieldErrors = {};
    if (!f.party_id) errs.party_id = "Choose the supplier.";
    const inst = textProblem(f.institution, 2, 80, true);
    if (inst) errs.institution = inst;
    const digits = f.account_number.replace(/[\s-]/g, "");
    if (!/^\d+$/.test(digits)) errs.account_number = "Use digits only (spaces and hyphens are ignored).";
    else if (f.kind === "bank" && (digits.length < 6 || digits.length > 20)) errs.account_number = "A bank account number has 6 to 20 digits.";
    else if (f.kind === "mobile_wallet" && !/^01[3-9]\d{8}$/.test(digits)) errs.account_number = "A mobile wallet number has 11 digits and starts with 013-019.";
    if (!f.change_source) errs.change_source = "Choose how these details arrived.";
    if (f.note.trim()) { const n = textProblem(f.note, 1, 200, true); if (n) errs.note = n; }
    if (Object.values(errs).some(Boolean)) return setErrors(errs);
    setBusy(true);
    const r = await api<Recorded>(`${base}/fraud/payee-destinations`, "POST", {
      party_id: f.party_id, kind: f.kind, institution: f.institution, account_number: f.account_number, change_source: f.change_source,
      ...(f.note.trim() ? { note: f.note } : {}),
    });
    setBusy(false);
    if (!r.ok) {
      const placed: FieldErrors = {};
      for (const [k, v] of Object.entries(r.errors)) placed[k in blank ? k : "_form"] ??= v;
      return setErrors(placed);
    }
    setF(blank);  // the number is cleared from the form as soon as it is saved
    setDone(r.data);
    onRecorded(r.data.flag_id);
  };

  return (
    <section className="card dest-card" aria-labelledby="dest-title">
      <div className="card-head">
        <h2 id="dest-title" className="section-title">Payee destinations</h2>
        <span className="badge badge-muted">Last 4 digits only</span>
      </div>
      {!data && <p className="muted">Loading…</p>}

      {data && data.parties.length === 0 && <p className="muted">No suppliers yet. Enter a bill in Records first.</p>}
      {data && data.parties.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Supplier</th><th>Current destination</th><th>Recorded</th><th>Changes</th></tr></thead>
            <tbody>
              {data.parties.map((p) => (
                <tr key={p.party_id}>
                  <td>{p.name}</td>
                  <td>{p.current ? `${p.current.institution} ${p.current.masked}` : <span className="muted">None recorded</span>}</td>
                  <td className="muted">{p.current ? `${fmtDate(p.current.date)} · ${p.current.recorded_by}` : "—"}</td>
                  <td className="num">{Math.max(p.history.length - 1, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {done && (
        <p className={`badge ${done.flag_id ? "badge-warning" : "badge-success"} block-badge`} role="status">
          Saved {done.institution} {done.masked} for {done.party}.{done.flag_id ? ` This changes the destination: a flag was raised and ${done.scan.holds_added} proposed payment(s) put on hold.` : ""}
        </p>
      )}

      {canWrite && data && data.parties.length > 0 && (
        <form className="stack-form" onSubmit={submit} noValidate autoComplete="off">
          <h3 className="ready-subtitle">Record a destination</h3>
          <div className="form-grid">
            <Field label="Supplier" error={errors.party_id}>
              <select value={f.party_id} onChange={(e) => set("party_id", e.target.value)}>
                <option value="">Choose…</option>
                {data.parties.map((p) => <option key={p.party_id} value={p.party_id}>{p.name}</option>)}
              </select>
            </Field>
            <Field label="Type" error={errors.kind}>
              <select value={f.kind} onChange={(e) => set("kind", e.target.value)}>
                <option value="bank">Bank account</option>
                <option value="mobile_wallet">Mobile wallet</option>
              </select>
            </Field>
            <Field label={f.kind === "bank" ? "Bank" : "Wallet provider"} error={errorFor(errors, "institution")}>
              <input value={f.institution} maxLength={90} onChange={(e) => set("institution", e.target.value)} />
            </Field>
            <Field label={f.kind === "bank" ? "Account number" : "Wallet number"} error={errorFor(errors, "account_number")}
                   hint="Used once to detect a change. Only the last 4 digits are kept.">
              <input value={f.account_number} inputMode="numeric" maxLength={40} autoComplete="off" spellCheck={false} onChange={(e) => set("account_number", e.target.value)} />
            </Field>
            <Field label="How these details arrived" error={errors.change_source}
                   hint={current ? `Current: ${current.institution} ${current.masked}. A different one is a change.` : "The first destination for a supplier is the baseline."}>
              <select value={f.change_source} onChange={(e) => set("change_source", e.target.value)}>
                <option value="">Choose…</option>
                {Object.entries(data.change_sources).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </Field>
            <Field label="Note (optional)" error={errorFor(errors, "note")}>
              <input value={f.note} maxLength={210} onChange={(e) => set("note", e.target.value)} />
            </Field>
          </div>
          {errors._form && <p className="badge badge-danger block-badge" role="alert">{errors._form}</p>}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save destination"}</button>
          </div>
        </form>
      )}
    </section>
  );
}

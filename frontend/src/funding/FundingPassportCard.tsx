import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Field } from "../settings/fields";

type Options = { version: string; fields: { id: string; label: string }[]; documents: { id: string; kind: string; where_kept: string; issued_on: string | null; expires_on: string | null }[];
  recipients: { id: string; label: string }[]; notice: string };
type Packet = { id: string; version: string; recipient: string; recipient_label: string; selected_fields: string[]; packet: Record<string, unknown>;
  packet_sha256: string; expires_at: string; notice: string };

export function FundingPassportCard({ base, canWrite }: { base: string; canWrite: boolean }) {
  const [options, setOptions] = useState<Options | null>(null);
  const [selected, setSelected] = useState<string[]>(["business_name", "jurisdiction", "base_currency", "cash_summary", "customer_dues", "supplier_bills"]);
  const [documents, setDocuments] = useState<string[]>([]);
  const [recipient, setRecipient] = useState("sandbox_lender_a");
  const [days, setDays] = useState("7");
  const [packet, setPacket] = useState<Packet | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [proposal, setProposal] = useState(false);

  useEffect(() => { api<Options>(`${base}/funding-passport`).then((r) => { if (r.ok) setOptions(r.data); else setError(r.message); }); }, [base]);
  const toggle = (arr: string[], value: string, setter: (next: string[]) => void) => setter(arr.includes(value) ? arr.filter((x) => x !== value) : [...arr, value]);
  const preview = async () => {
    setBusy(true); setError(""); setProposal(false);
    const r = await api<Packet>(`${base}/funding-passport/packets`, "POST", { recipient, selected_fields: selected, document_ids: documents, expires_in_days: Number(days) });
    setBusy(false); if (!r.ok) return setError(r.message); setPacket(r.data);
  };
  const propose = async () => {
    if (!packet) return;
    setBusy(true); setError("");
    const r = await api(`${base}/approvals`, "POST", { kind: "funding_packet_share", packet_id: packet.id });
    setBusy(false); if (!r.ok) return setError(r.message); setProposal(true);
  };
  return <section className="card" aria-labelledby="passport-title" style={{ marginTop: 16 }}>
    <h2 id="passport-title" className="card-title">Funding Passport</h2>
    <p className="muted small">Choose which business information and evidence goes into a versioned lender preview. Account numbers, personal ID numbers, customer names, and invoice numbers are not included. Sharing requires a separate owner approval and sandbox run.</p>
    {error && <p className="badge badge-danger block-badge" role="alert">{error}</p>}
    {options && <>
      <fieldset className="adverse-set"><legend>Choose sections to include</legend>{options.fields.map((f) => <label key={f.id} className="check-row"><input type="checkbox" checked={selected.includes(f.id)} onChange={() => toggle(selected, f.id, setSelected)} /> {f.label}</label>)}</fieldset>
      {options.documents.length > 0 && <fieldset className="adverse-set" style={{ marginTop: 12 }}><legend>Choose evidence register entries</legend>{options.documents.map((d) => <label key={d.id} className="check-row"><input type="checkbox" checked={documents.includes(d.id)} onChange={() => toggle(documents, d.id, setDocuments)} /> {d.kind.replace(/_/g, " ")} · {d.where_kept}</label>)}</fieldset>}
      {options.documents.length === 0 && <p className="muted small">No active evidence entries. Record documents in the Documents for financing section if you want to include their register details.</p>}
      <div className="form-grid" style={{ marginTop: 12 }}>
        <Field label="Simulated recipient"><select value={recipient} onChange={(e) => setRecipient(e.target.value)}>{options.recipients.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select></Field>
        <Field label="Preview expires in days"><input inputMode="numeric" min="1" max="30" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
      </div>
      {canWrite && <div className="form-actions"><button type="button" className="btn btn-primary" onClick={preview} disabled={busy || selected.length === 0}>{busy ? "Preparing…" : "Build exact preview"}</button></div>}
      {!canWrite && <p className="muted small">Only the owner can assemble a Funding Passport packet.</p>}
    </>}
    {packet && <div className="alert-card alert-low" style={{ marginTop: 14 }}>
      <h3 className="card-title">Exact packet preview · {packet.version}</h3>
      <p className="small">Recipient: {packet.recipient_label} · Expires: {new Date(packet.expires_at).toLocaleString("en-GB")}</p>
      <pre className="packet-preview">{JSON.stringify(packet.packet, null, 2)}</pre>
      <p className="muted small">Disclosure fingerprint: {packet.packet_sha256}</p>
      {canWrite && !proposal && <button type="button" className="btn btn-secondary" onClick={propose} disabled={busy}>{busy ? "Requesting approval…" : "Request owner approval to share"}</button>}
      {proposal && <p className="badge badge-success">Approval proposal created. Review it in <Link to="/approvals-audit">Approvals &amp; Audit</Link>.</p>}
    </div>}
  </section>;
}

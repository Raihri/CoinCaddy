import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import type { Obligation } from "../obligations/types";
import { exponentOf, Field } from "../settings/fields";

type Contact = { id: string; party_id: string; channel: "email" | "sms"; address_masked: string;
  status: "unverified" | "verified" | "rejected"; current: boolean; method: string | null; reviewed_by: string | null;
  reject_reason: string | null };
type History = { draft_id: string; state: string; channel: string; stage: number; proposal_id: string | null;
  sent_in_sandbox: boolean; sent_at: string | null; execution: { outcome: string; reference: string | null } | null };
type Invoice = Obligation & { bucket: string; blockers: string[]; eligible: boolean; history: History[] };
type Customer = { party: Obligation["party"]; invoices: Invoice[];
  contacts: { email: Contact | null; sms: Contact | null }; has_verified_contact: boolean };
type Suggestion = { rank: number; party_id: string; customer: string; reason: string; next_step: string; invoice_count: number };
type Aging = { currency: string; buckets: Record<string, { amount_minor: number; count: number }>;
  disputed: { amount_minor: number; count: number }; total_minor: number };
type Overview = { as_of: string; rules_version: string; cooldown_days: number; notice: string;
  aging: Aging[]; suggestions: Suggestion[]; customers: Customer[]; overdue_count: number; disputed_count: number };
type Draft = { id: string; obligation_id: string; channel: "email" | "sms"; message: string; state: string;
  created_at: string; created_by: string; contact: Contact | null; proposal_id: string | null;
  sent_in_sandbox: boolean; execution: { outcome: string; reference: string | null } | null };

const BUCKETS = [["current", "Not yet due"], ["1-30", "1–30 days"], ["31-60", "31–60 days"],
  ["61-90", "61–90 days"], ["91+", "Over 90 days"]] as const;

export function ReceivablesGuardianPage() {
  const { active, options } = useSession();
  const [params, setParams] = useSearchParams();
  const base = active ? `/api/businesses/${active.business_id}/guardian` : "";
  const canRead = active?.allowed_actions.includes("guardian.read") ?? false;
  const canDraft = active?.allowed_actions.includes("guardian.draft") ?? false;
  const canWriteContact = active?.allowed_actions.includes("contact.write") ?? false;
  const canVerify = active?.allowed_actions.includes("contact.verify") ?? false;
  const canPropose = active?.allowed_actions.includes("approvals.propose") ?? false;
  const [data, setData] = useState<Overview | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [channel, setChannel] = useState<"email" | "sms">("email");
  const [address, setAddress] = useState("");
  const [note, setNote] = useState("");
  const [expires, setExpires] = useState("");
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));

  const load = useCallback(async () => {
    if (!base || !canRead) return;
    const r = await api<Overview>(base);
    if (r.ok) setData(r.data); else setError(r.message);
  }, [base, canRead]);
  useEffect(() => { setData(null); setError(null); load(); }, [load]);
  const invoiceId = params.get("invoice") ?? "";
  const selected = data?.customers.flatMap((c) => c.invoices.map((i) => ({ invoice: i, customer: c })))
    .find((x) => x.invoice.id === invoiceId);
  const selectedInvoice = selected?.invoice;
  const selectedCustomer = selected?.customer;
  useEffect(() => {
    setDrafts([]);
    if (!base || !canRead || !invoiceId) return;
    api<Draft[]>(`${base}/drafts?obligation_id=${encodeURIComponent(invoiceId)}`).then((r) => {
      if (r.ok) setDrafts(r.data);
    });
  }, [base, canRead, invoiceId]);
  const refresh = async () => {
    await load();
    if (invoiceId) {
      const r = await api<Draft[]>(`${base}/drafts?obligation_id=${encodeURIComponent(invoiceId)}`);
      if (r.ok) setDrafts(r.data);
    }
  };
  const act = async (url: string, body: object, success?: () => void) => {
    setBusy(true); setError(null);
    const r = await api<unknown>(url, "POST", body);
    setBusy(false);
    if (!r.ok) { setError(r.message); return; }
    success?.();
    await refresh();
  };
  const select = (id: string) => { setParams(id ? { invoice: id } : {}); setError(null); setNote(""); };

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">Receivables Guardian</h1>
          <p className="page-sub">See who owes you money, prepare a factual reminder, and track its sandbox approval.</p></div>
        {canRead && <button type="button" className="btn btn-ghost" onClick={refresh}>Refresh</button>}
      </div>
      {!canRead && <section className="card"><p>Only owners and bookkeepers can view customer collections.</p></section>}
      {error && <p className="badge badge-danger" role="alert">{error}</p>}
      {canRead && !data && !error && <p className="muted">Loading customer dues…</p>}
      {data && <>
        <p className="muted small">{data.notice} Calculated {fmtDate(data.as_of)} · {data.rules_version}</p>
        <section className="card" aria-labelledby="aging-title">
          <h2 id="aging-title" className="card-title">Outstanding by age</h2>
          {data.aging.length === 0 && <p className="muted">No outstanding customer invoices.</p>}
          {data.aging.map((a) => <div key={a.currency} className="table-wrap"><table className="table">
            <thead><tr><th>{a.currency}</th>{BUCKETS.map(([key, label]) => <th key={key} className="right">{label}</th>)}<th className="right">Disputed</th><th className="right">Total</th></tr></thead>
            <tbody><tr><th>Outstanding</th>{BUCKETS.map(([key]) => <td key={key} className="num right">{money(a.buckets[key].amount_minor, a.currency)} <span className="muted small">({a.buckets[key].count})</span></td>)}
              <td className="num right">{money(a.disputed.amount_minor, a.currency)} <span className="muted small">({a.disputed.count})</span></td>
              <td className="num right"><strong>{money(a.total_minor, a.currency)}</strong></td></tr></tbody>
          </table></div>)}
        </section>
        <section className="card" style={{ marginTop: 16 }} aria-labelledby="suggestions-title">
          <h2 id="suggestions-title" className="card-title">Who to contact first</h2>
          {data.suggestions.length === 0 && <p className="muted">No undisputed overdue invoice is ready for a reminder.</p>}
          <ol className="queue-list">{data.suggestions.map((s) => {
            const customer = data.customers.find((c) => c.party.id === s.party_id);
            const first = customer?.invoices.find((i) => i.eligible);
            return <li key={s.party_id} className="queue-item"><strong>{s.rank}. {s.customer}</strong> · {s.reason}
              <span className="muted small block">{s.next_step}</span>
              {first && <button type="button" className="btn btn-ghost btn-small" onClick={() => select(first.id)}>Review invoice {first.document_number}</button>}</li>;
          })}</ol>
        </section>
        <section className="card" style={{ marginTop: 16 }} aria-labelledby="invoices-title">
          <h2 id="invoices-title" className="card-title">Customer invoices</h2>
          <div className="table-wrap"><table className="table"><thead><tr><th>Invoice</th><th>Customer</th><th>Due</th><th className="right">Outstanding</th><th>Status</th><th /></tr></thead>
            <tbody>{data.customers.flatMap((c) => c.invoices.map((i) => <tr key={i.id}>
              <td><Link className="link" to={`/records/obligations/${i.id}`}>{i.document_number}</Link></td><td>{c.party.name}</td>
              <td>{fmtDate(i.due_date)}</td><td className="num right">{money(i.outstanding_minor, i.currency)}</td>
              <td>{i.disputed ? <span className="badge badge-warning">Disputed · held</span> : i.days_overdue > 0 ? `${i.days_overdue} days overdue` : "Not yet due"}</td>
              <td><button type="button" className="link-button" onClick={() => select(i.id)}>Review</button></td></tr>))}</tbody>
          </table></div>
        </section>
      </>}
      {selectedInvoice && selectedCustomer && <>
        <section className="card" style={{ marginTop: 16 }} aria-labelledby="selected-title">
          <h2 id="selected-title" className="card-title">{selectedInvoice.document_number} · {selectedCustomer.party.name}</h2>
          <p>Outstanding <strong>{money(selectedInvoice.outstanding_minor, selectedInvoice.currency)}</strong> · due {fmtDate(selectedInvoice.due_date)}</p>
          {selectedInvoice.blockers.length > 0 ? <ul>{selectedInvoice.blockers.map((b) => <li key={b}>{b}</li>)}</ul> :
            <p className="badge badge-success">Eligible to draft a reminder after contact verification.</p>}
          <h3 className="card-title">Customer contact</h3>
          <p className="muted small">Only a masked address is stored. Recording a contact does not verify it; an owner must record how it was checked.</p>
          {(["email", "sms"] as const).map((ch) => <div key={ch} className="alert-card alert-low">
            <strong>{ch === "email" ? "Email" : "SMS"}</strong> · {selectedCustomer.contacts[ch]?.address_masked ?? "No contact"}
            {selectedCustomer.contacts[ch] && <> <span className={`badge ${selectedCustomer.contacts[ch]?.status === "verified" ? "badge-success" : "badge-warning"}`}>
              {selectedCustomer.contacts[ch]?.status}</span>
              {selectedCustomer.contacts[ch]?.status === "rejected" && <span className="muted small"> {selectedCustomer.contacts[ch]?.reject_reason}</span>}
              {canVerify && selectedCustomer.contacts[ch]?.status === "unverified" &&
                <ContactReview base={base} contact={selectedCustomer.contacts[ch]!} onDone={refresh} />}</>}
          </div>)}
          {canWriteContact && <form className="form-grid" onSubmit={(e: FormEvent) => {
            e.preventDefault(); if (!address.trim()) { setError("Enter the customer contact."); return; }
            act(`${base}/customers/${selectedCustomer.party.id}/contacts`, { channel, address }, () => setAddress(""));
          }} aria-label="Record customer contact">
            <Field label="Channel"><select value={channel} onChange={(e) => setChannel(e.target.value as "email" | "sms")}>
              <option value="email">Email</option><option value="sms">SMS</option></select></Field>
            <Field label="New contact address"><input value={address} onChange={(e) => setAddress(e.target.value)} placeholder={channel === "email" ? "customer@example.com" : "+8801…"} maxLength={120} /></Field>
            <div className="form-actions span-2"><button type="submit" className="btn btn-ghost" disabled={busy}>Record contact for verification</button></div>
          </form>}
        </section>
        <section className="card" style={{ marginTop: 16 }} aria-labelledby="draft-title">
          <h2 id="draft-title" className="card-title">Reminder drafts</h2>
          <p className="muted small">Drafts remain internal. A proposal needs owner approval and release before an owner can run it in the sandbox. Sending a reminder never records a payment.</p>
          {canDraft && selectedInvoice.blockers.length === 0 && <form className="form-grid" onSubmit={(e: FormEvent) => {
            e.preventDefault(); act(`${base}/drafts`, { obligation_id: selectedInvoice.id, channel, note: note.trim() || null }, () => setNote(""));
          }} aria-label="Create reminder draft">
            <Field label="Verified contact channel"><select value={channel} onChange={(e) => setChannel(e.target.value as "email" | "sms")}>
              <option value="email">Email</option><option value="sms">SMS</option></select></Field>
            <Field label="Optional note"><input value={note} onChange={(e) => setNote(e.target.value)} maxLength={150} placeholder="A short factual note" /></Field>
            <div className="form-actions span-2"><button type="submit" className="btn btn-primary" disabled={busy || selectedCustomer.contacts[channel]?.status !== "verified"}>Create internal draft</button></div>
          </form>}
          {drafts.length === 0 && <p className="muted">No drafts for this invoice.</p>}
          {drafts.map((d) => <article className="alert-card alert-low" key={d.id}>
            <p className="muted small">{d.channel.toUpperCase()} to {d.contact?.address_masked ?? "contact no longer available"} · {d.state.replace(/_/g, " ")} · drafted by {d.created_by}</p>
            <p>{d.message}</p>
            {d.proposal_id ? <p><Link className="link" to="/approvals-audit">Review approval and sandbox outcome</Link>
              {d.execution && <> · {d.execution.outcome}{d.execution.reference ? ` · ${d.execution.reference}` : ""}</>}</p>
              : canPropose && <div className="form-actions"><Field label="Approval expires in (hours, optional)"><input inputMode="numeric" value={expires} onChange={(e) => setExpires(e.target.value)} placeholder="48" /></Field>
                  <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => {
                    const hours = expires.trim() ? Number(expires) : null;
                    if (hours !== null && (!Number.isInteger(hours) || hours < 1 || hours > 72)) { setError("Enter 1–72 whole hours."); return; }
                    act(`${base}/drafts/${d.id}/propose`, { expires_in_hours: hours });
                  }}>Send draft for owner approval</button></div>}
          </article>)}
        </section>
      </>}
    </>
  );
}

function ContactReview({ base, contact, onDone }: { base: string; contact: Contact; onDone: () => void }) {
  const [method, setMethod] = useState("called_known_contact");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (body: object) => {
    setBusy(true); setError(null);
    const r = await api(`${base}/contacts/${contact.id}/review`, "POST", body);
    setBusy(false);
    if (r.ok) onDone(); else setError(r.message);
  };
  return <div className="form-grid" style={{ marginTop: 12 }}>
    <Field label="How you verified it"><select value={method} onChange={(e) => setMethod(e.target.value)}>
      <option value="called_known_contact">Called a known contact</option><option value="met_in_person">Met in person</option>
      <option value="written_confirmation">Written confirmation</option></select></Field>
    <Field label="Verification reference"><input value={reference} onChange={(e) => setReference(e.target.value)} maxLength={120} placeholder="Call log or confirmation reference" /></Field>
    <div className="form-actions span-2"><button type="button" className="btn btn-ghost" disabled={busy || reference.trim().length < 3}
      onClick={() => submit({ decision: "verify", method, reference })}>Verify contact</button></div>
    <Field label="Reason to reject"><input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={120} /></Field>
    <div className="form-actions"><button type="button" className="btn btn-ghost" disabled={busy || reason.trim().length < 3}
      onClick={() => submit({ decision: "reject", reason })}>Reject contact</button></div>
    {error && <p className="badge badge-danger span-2" role="alert">{error}</p>}
  </div>;
}

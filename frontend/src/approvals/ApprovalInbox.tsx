import { useState } from "react";
import { statusLine, type Proposal } from "./types";

// The Approval inbox card of images/08-approvals-and-audit.png: tier chip, title, one line of detail, a coloured
// status line in words, and a Review button. Closed proposals are behind a toggle so the inbox stays short.
export function ApprovalInbox({ items, pendingCount, money, selected, onReview, canPropose, onPropose }: {
  items: Proposal[]; pendingCount: number; money: (minor: number, currency: string) => string; selected: string | null;
  onReview: (id: string) => void; canPropose: boolean; onPropose: () => void;
}) {
  const [showClosed, setShowClosed] = useState(false);
  const open = items.filter(isOpen);
  const closed = items.filter((p) => !isOpen(p));
  const shown = showClosed ? [...open, ...closed] : open;
  return (
    <section className="card inbox-card" aria-labelledby="inbox-title">
      <div className="card-head">
        <h2 id="inbox-title" className="section-title">Approval inbox</h2>
        <span className="badge badge-warning">{pendingCount} pending</span>
      </div>
      {shown.length === 0 && (
        <p className="muted">{closed.length ? "Nothing waiting for a decision." : "No proposals yet."} Proposals record an exact action; an approved one runs only in the sandbox.</p>
      )}
      <ul className="inbox-list">
        {shown.map((p) => {
          const s = statusLine(p);
          return (
            <li key={p.id} className={`inbox-item${selected === p.id ? " is-selected" : ""}`}>
              <span className={`tier-chip tier-${p.tier}`}>{p.tier}</span>
              <div className="inbox-body">
                <h3 className="inbox-title">{p.title}</h3>
                <p className="inbox-sub">{subtitle(p, money)}</p>
                <div className="inbox-foot">
                  <span className={`inbox-status ${s.cls}`}>{s.text}</span>
                  <button type="button" className="btn btn-primary btn-review" onClick={() => onReview(p.id)} aria-label={`Review: ${p.title}`}>Review</button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
      <div className="inbox-actions">
        {closed.length > 0 && (
          <button type="button" className="btn btn-ghost btn-small" onClick={() => setShowClosed((v) => !v)} aria-expanded={showClosed}>
            {showClosed ? "Hide closed" : `Show closed (${closed.length})`}
          </button>
        )}
        {canPropose && <button type="button" className="btn btn-ghost btn-small" onClick={onPropose}>New proposal</button>}
      </div>
    </section>
  );
}

// Still needs someone: a decision, a release, or a sandbox run that has no final outcome yet.
function isOpen(p: Proposal): boolean {
  if (p.state === "pending" || p.state === "approved") return true;
  return p.state === "executable" && !(p.execution && (p.execution.outcome === "succeeded" || p.execution.outcome === "rejected"));
}

export function subtitle(p: Proposal, format: (minor: number, currency: string) => string): string {
  const money = (m: number, c: string) => format(m, c).replace(" ", " ");  // keep the currency sign with the amount
  const d = p.payload as Record<string, string & number>;
  switch (p.kind) {
    case "invoice_reminder":
      return `Customer: ${d.customer} · ${d.document_number} · ${money(d.outstanding_minor, d.currency)} by ${d.channel === "sms" ? "SMS" : "email"}`;
    case "supplier_payment":
      return `${d.supplier} · ${money(d.amount_minor, d.currency)} · ${d.document_number} from ${d.from_account}`;
    case "financing_application":
      return `Recipient: ${d.recipient} · ${d.product} · ${money(d.amount_minor, d.currency)}`;
  }
}

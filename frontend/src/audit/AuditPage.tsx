import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ApprovalInbox } from "../approvals/ApprovalInbox";
import { ProposalReview } from "../approvals/ProposalReview";
import { ProposeForm } from "../approvals/ProposeForm";
import type { Inbox, Policy, Proposal } from "../approvals/types";
import { ROLE_LABELS, useSession, type Membership } from "../auth/session";
import { ExecutionLog } from "../executor/ExecutionLog";
import type { Adapters } from "../executor/types";
import { formatMoney } from "../money";
import { exponentOf } from "../settings/fields";

type AuditEvent = {
  seq: number;
  id: string;
  occurred_at: string;
  actor_name: string | null;
  actor_role: Membership["role"] | null;
  action: string;
  outcome: "success" | "denied" | "not_found" | "conflict" | "failed";
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  event_hash: string;
};
type Page = { events: AuditEvent[]; next_before_seq: number | null };

const OUTCOME_BADGE: Record<AuditEvent["outcome"], [string, string]> = {
  success: ["badge-success", "Success"],
  denied: ["badge-danger", "Denied"],
  failed: ["badge-danger", "Failed"],
  conflict: ["badge-warning", "Refused"],
  not_found: ["badge-warning", "Not found"],
};

const SETTING_LABELS: Record<string, string> = {
  name: "name",
  jurisdiction: "jurisdiction",
  base_currency: "base currency",
  timezone: "timezone",
  reserve_amount_minor: "reserve",
  reserve_currency: "reserve",
};

// Short pages keep the history beside the inbox, as in the mockup; older events load on request.
const HISTORY_PAGE = 8;

const APPROVAL_KIND: Record<string, string> = {
  invoice_reminder: "invoice reminder",
  supplier_payment: "supplier payment",
  financing_application: "financing application",
  funding_packet_share: "Funding Passport packet",
};

const FRAUD_RULE: Record<string, string> = {
  duplicate_payment: "possible duplicate",
  unusual_payee_amount: "unusual amount",
  split_near_limit: "split payment",
  lookalike_payee: "lookalike payee",
  destination_change: "destination change",
  paid_without_settlement: "missing settlement",
};

const roleLabel =(r: unknown) => ROLE_LABELS[r as Membership["role"]] ?? String(r);

// Human description built from structured fields; the server stores codes, not prose.
function describe(e: AuditEvent): string {
  const d = e.details;
  switch (e.action === "import.map" ? "import.map" : `${e.action}:${e.outcome}`) {
    case "membership.role_change:success":
      return `Changed ${d.member_name ?? "a member"}'s role from ${roleLabel(d.from_role)} to ${roleLabel(d.to_role)}`;
    case "membership.role_change:conflict":
      return `Tried to make ${d.member_name ?? "the last owner"} ${roleLabel(d.to_role)}: a business must keep one owner`;
    case "membership.role_change:not_found":
      return "Tried to change a membership that is not in this business";
    case "membership.manage:denied":
      return "Tried to change a member's role without permission";
    case "business.export:success":
      return `Exported business data (${d.account_count} accounts, ${d.member_count} members)`;
    case "business.export:denied":
      return "Tried to export business data without permission";
    case "audit.read:denied":
      return "Tried to open the audit history without permission";
    case "business.create:success":
      return `Created the business "${d.name}" (${d.base_currency}, ${d.timezone})`;
    case "business.settings_update:success": {
      const changed = Object.keys((d.changes as Record<string, unknown>) ?? {})
        .map((k) => SETTING_LABELS[k] ?? k)
        .filter((v, i, a) => a.indexOf(v) === i);
      return `Updated business settings: ${changed.join(", ")} (now version ${d.version})`;
    }
    case "business.settings_update:conflict":
      return `Tried to save settings from an outdated screen (version ${d.expected_version}, current ${d.current_version})`;
    case "business.settings.update:denied":
      return "Tried to change business settings without permission";
    case "account.create:success":
      return `Added cash account "${d.label}"`;
    case "account.create:conflict":
      return `Tried to add a second account named "${d.label}"`;
    case "account.relabel:success":
      return `Renamed account "${d.from}" to "${d.to}"`;
    case "account.relabel:conflict":
      return `Tried to rename "${d.from}" to "${d.to}", which is already used`;
    case "import.upload:success":
      return d.duplicate_upload
        ? `Uploaded "${d.source_name}" again; the existing import was reused`
        : `Uploaded "${d.source_name}" (${d.rows} rows) to ${d.account}`;
    case "import.upload:failed":
      return `Rejected upload "${d.source_name}" (${String(d.reason).replace(/_/g, " ")})`;
    case "import.map":
      return `Mapped columns: ${d.valid_rows} valid, ${d.error_rows} rejected, ${d.possible_duplicates} possible duplicates`;
    case "import.row_decision:success":
      return `Set row ${d.row_number} to ${d.to === "include" ? "post" : "skip"}${d.possible_duplicate ? " (possible duplicate)" : ""}`;
    case "import.commit:success":
      return `Posted ${d.posted_count} rows to the ledger`;
    case "import.commit:conflict":
      return `Posting refused: ${d.reason}`;
    case "import.discard:success":
      return `Discarded an import of ${d.rows} rows`;
    case "import.stage:denied":
    case "import.review:denied":
    case "import.commit:denied":
      return `Tried to ${e.action.split(".")[1]} imported records without permission`;
    case "cash.anchor_add:success":
      return `Entered a balance for ${d.account} at the end of ${d.anchor_date}`;
    case "cash.coverage_add:success":
      return `Declared ${d.account} complete from ${d.period_start} to ${d.period_end}`;
    case "cash.restriction_add:success":
      return `Restricted funds in ${d.account} from ${d.effective_from}`;
    case "cash.transfer_add:success":
      return d.matched ? "Linked both sides of an own-account transfer" : "Declared a one-sided own-account transfer";
    case "cash.balance_anchor_void:success":
    case "cash.coverage_period_void:success":
    case "cash.restricted_fund_void:success":
    case "cash.own_transfer_void:success":
      return `Voided a ${e.action.split(".")[1].replace("_void", "").replace(/_/g, " ")}: ${d.reason}`;
    case "obligation.create:success":
      return `Entered ${d.direction === "receivable" ? "invoice" : "bill"} ${d.document_number} for ${d.party}`;
    case "obligation.allocate:success":
      return `Recorded a payment; ${d.outstanding_after_minor} minor units still outstanding`;
    case "obligation.allocate:conflict":
      return `Refused a payment that would over-allocate (outstanding ${d.outstanding_minor})`;
    case "obligation.reverse:success":
      return `Reversed a payment: ${d.reason}`;
    case "obligation.dispute:success":
      return d.disputed ? `Marked as disputed: ${d.reason}` : "Cleared a dispute";
    case "obligation.cancel:success":
      return `Cancelled an invoice or bill: ${d.reason}`;
    case "obligations.write:denied":
      return "Tried to change invoices or bills without permission";
    case "ledger.classify:success":
      return `Classified ${d.count} posted transaction(s)`;
    case "ledger.classify:denied":
      return "Tried to classify transactions without permission";
    case "outlook.snapshot:success":
      return `Calculated a cash outlook for ${d.as_of}${d.expected_breach ? " (reserve breach on the expected path)" : d.cautious_breach ? " (breach only on the cautious path)" : ""}`;
    case "paymentreview.create:success":
      return `Saved a payment review: ${String(d.decision).replace(/_/g, " ")}${d.linked_payable ? " (linked to a bill)" : ""}`;
    case "paymentreview.run:denied":
      return "Tried to run a payment review without permission";
    case "funding.run:success":
      return `Compared financing options: ${d.eligible} eligible, ${d.excluded} excluded (illustrative, nothing applied for)`;
    case "funding.run:denied":
      return "Tried to compare financing options without permission";
    case "rescue.run:success":
      return `Compared rescue options: ${d.recommendation === "recommended" ? `preferred ${d.recommended}` : "no safe recommendation"} (proposals only)`;
    case "rescue.run:denied":
      return "Tried to run a rescue plan without permission";
    case "obligation.deferral_permit:success":
      return `Documented a supplier's permission to pay a bill later (${d.reference}, by ${d.agreed_due_date})`;
    case "obligation.deferral_void:success":
      return `Voided a documented payment-later permission: ${d.reason}`;
    case "alerts.run:success":
      return `Checked alerts: ${d.created} new, ${d.updated} changed, ${d.resolved} cleared`;
    case "alerts.run:denied":
      return "Tried to run the alert check without permission";
    case "outlook.run:denied":
      return "Tried to calculate a cash outlook without permission";
    case "cash.manage:denied":
      return "Tried to change cash records without permission";
    case "approvals.propose:success":
      return `Proposed a ${d.tier} action: ${APPROVAL_KIND[d.kind as string] ?? d.kind} (nothing sent or paid)`;
    case "approvals.decide:success":
      return `${d.decision === "approved" ? "Approved" : "Rejected"} a ${d.tier} ${APPROVAL_KIND[d.kind as string] ?? "action"}${d.reauthenticated ? " after re-authentication" : ""}`;
    case "approvals.decide:conflict":
      return `Approval refused: ${d.reason}`;
    case "approvals.reauth:failed":
      return `Re-authentication failed for a ${d.tier} approval (${d.reason})`;
    case "approvals.withdraw:success":
      return `Withdrew a proposal: ${d.reason}`;
    case "approvals.release:success":
      return `Released a ${d.tier} ${APPROVAL_KIND[d.kind as string] ?? "action"} as executable after ${d.checks_passed} checks (not executed)`;
    case "approvals.release:conflict":
      return `Release refused: ${d.failed_checks ? (d.failed_checks as string[]).join(", ").replace(/_/g, " ") : d.reason}`;
    case "passport.packet_create:success":
      return `Built Funding Passport ${d.version} preview for ${d.recipient} with ${(d.fields as string[]).join(", ")}`;
    case "approvals.read:denied":
    case "approvals.propose:denied":
    case "approvals.decide:denied":
    case "approvals.release:denied":
      return `Tried to ${e.action.split(".")[1]} an approval without permission`;
    case "fraud.scan:success":
      return `Ran fraud checks (${d.rules_version}): ${d.flags_created} new flag(s)${d.holds_added ? `, ${d.holds_added} payment(s) put on hold` : ""}`;
    case "fraud.review:success":
      return `${d.decision === "rejected" ? "Cleared" : d.decision === "confirmed" ? "Marked for investigation" : "Deferred"} a ${FRAUD_RULE[d.rule as string] ?? "fraud"} flag: ${d.reason}${d.hold_released ? " (hold lifted)" : ""}`;
    case "fraud.review:conflict":
    case "fraud.review:failed":
      return `Fraud flag review refused: ${d.reason}`;
    case "payees.destination_record:success":
      return `Recorded ${d.is_change ? "a changed" : "the first"} ${d.kind === "mobile_wallet" ? "wallet" : "bank"} destination for ${d.party}: ${d.institution} ${d.account_number}${d.flag_created ? " (flagged for verification)" : ""}`;
    case "fraud.read:denied":
    case "fraud.run:denied":
    case "fraud.review:denied":
    case "payees.destinations.write:denied":
      return "Tried to use the Fraud Monitor without permission";
    case "executor.execute:success":
      return `Ran a ${d.tier} ${APPROVAL_KIND[d.kind as string] ?? "action"} in the sandbox: ${d.reference} (nothing real sent or paid)`;
    case "executor.execute:failed":
      return d.outcome === "rejected"
        ? `The sandbox refused a ${APPROVAL_KIND[d.kind as string] ?? "action"} (final)`
        : `The sandbox gave no answer for a ${APPROVAL_KIND[d.kind as string] ?? "action"}; retry with the same key`;
    case "executor.execute:conflict":
      return `Sandbox run blocked, nothing sent: ${((d.failed_checks as string[]) ?? []).join(", ").replace(/_/g, " ")}`;
    case "executor.replay:success":
      return `Retried a sandbox ${APPROVAL_KIND[d.kind as string] ?? "action"} with the same key: same outcome${d.reference ? ` (${d.reference})` : ""}, nothing sent again`;
    case "executor.run:denied":
    case "executor.read:denied":
      return "Tried to use the sandbox executor without permission";
    case "guardian.contact_add:success":
      return `Recorded a ${d.channel} contact for ${d.customer}: ${d.address} (verification required)`;
    case "guardian.contact_review:success":
      return `${d.decision === "verify" ? "Verified" : "Rejected"} a ${d.channel} contact: ${d.address}`;
    case "guardian.draft_create:success":
      return `Created an internal reminder draft for ${d.document_number} (${d.channel}, stage ${d.stage})`;
    case "guardian.draft_propose:success":
      return `Sent a reminder draft for ${d.document_number} to the approval inbox`;
    case "guardian.read:denied":
    case "guardian.draft:denied":
    case "contact.write:denied":
    case "contact.verify:denied":
      return "Tried to use Receivables Guardian without permission";
    case "account.relabel:not_found":
      return "Tried to rename an account that is not in this business";
    default:
      return `${e.action} (${e.outcome})`;
  }
}

// Layout follows images/08-approvals-and-audit.png: Approval inbox on the left; Funding Passport preview and
// Activity history on the right. The proposal form and the review panel sit below that layout.
export function AuditPage() {
  const { active, profile, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}` : null;
  const allowed = active?.allowed_actions ?? [];
  const can = (a: string) => allowed.includes(a);
  const canReadApprovals = can("approvals.read");
  const [timezone, setTimezone] = useState<string | undefined>(undefined);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [adapters, setAdapters] = useState<Adapters | null>(null);
  const [selected, setSelected] = useState<Proposal | null>(null);
  const [proposing, setProposing] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [inboxError, setInboxError] = useState<string | null>(null);
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));

  useEffect(() => {
    if (!base) return;
    fetch(base).then((r) => (r.ok ? r.json() : null)).then((b: { timezone?: string } | null) => setTimezone(b?.timezone));
  }, [base]);

  const loadInbox = useCallback(async () => {
    if (!base || !canReadApprovals) return;
    const [l, pol] = await Promise.all([api<Inbox>(`${base}/approvals`), api<Policy>(`${base}/approvals/policy`)]);
    if (l.ok) { setInbox(l.data); setInboxError(null); } else setInboxError(l.message);
    if (pol.ok) setPolicy(pol.data);
  }, [base, canReadApprovals]);
  useEffect(() => { setSelected(null); setProposing(false); setInbox(null); loadInbox(); }, [loadInbox]);
  useEffect(() => {
    if (!base || !can("executor.read")) return setAdapters(null);
    api<Adapters>(`${base}/sandbox/adapters`).then((r) => setAdapters(r.ok ? r.data : null));
  }, [base, allowed.join()]);

  const fmt = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: timezone });
  const fmtTime = (iso: string) => fmt.format(new Date(iso));
  const scrollTo = (id: string) => requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  const show = (id: string) => {
    if (!base) return;
    api<Proposal>(`${base}/approvals/${id}`).then((r) => {
      if (!r.ok) return setInboxError(r.message);
      setSelected(r.data);
      setProposing(false);
      scrollTo("proposal-review");
    });
  };
  const changed = (p: Proposal) => { setSelected(p); loadInbox(); setHistoryKey((k) => k + 1); };

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Approvals &amp; Audit</h1>
          <p className="page-sub">Owner-controlled actions with a complete record of decisions</p>
        </div>
      </div>

      <div className="approvals-grid">
        <div className="approvals-col">
          {inboxError && <p className="badge badge-danger block-badge" role="alert">{inboxError}</p>}
          {!canReadApprovals && (
            <section className="card" aria-labelledby="inbox-title">
              <h2 id="inbox-title" className="section-title">Approval inbox</h2>
              <p className="badge badge-info block-badge" role="status">Only owners and bookkeepers can see proposals. Only owners approve them.</p>
            </section>
          )}
          {canReadApprovals && !inbox && (
            <section className="card"><h2 className="section-title">Approval inbox</h2><p className="muted">Loading…</p></section>
          )}
          {canReadApprovals && inbox && (
            <ApprovalInbox items={inbox.items} pendingCount={inbox.counts.pending} money={money} selected={selected?.id ?? null}
                           onReview={show} canPropose={can("approvals.propose")}
                           onPropose={() => { setProposing(true); setSelected(null); scrollTo("propose-title"); }} />
          )}
        </div>

        <div className="approvals-col">
          <section className="card" aria-labelledby="passport-title">
            <h2 id="passport-title" className="section-title">Funding Passport preview</h2>
            <span className="badge badge-info">Preview on Funding Matches</span>
            <p className="muted">Assemble and review the selected packet in Funding Matches. Sharing requires owner approval and runs only through the sandbox.</p>
          </section>
          <ActivityHistory base={base} canRead={can("audit.read")} businessName={active?.business_name} timezone={timezone} refreshKey={historyKey} />
        </div>
      </div>

      {proposing && base && (
        <ProposeForm base={base} accounts={profile?.accounts ?? []} money={money} exponent={(c) => exponentOf(options, c)}
                     canReadReviews={can("paymentreview.read")} canReadReadiness={can("readiness.read")}
                     onCreated={(p) => { setProposing(false); changed(p); show(p.id); }} onCancel={() => setProposing(false)} />
      )}
      {selected && base && (
        <ProposalReview base={base} proposal={selected} policy={policy} money={money} fmtTime={fmtTime} timezone={timezone} canRun={can("executor.run")}
                        onChanged={changed} onClose={() => setSelected(null)} />
      )}
      {base && can("executor.read") && <ExecutionLog base={base} adapters={adapters} refreshKey={historyKey} fmtTime={fmtTime} />}
    </>
  );
}

function ActivityHistory({ base, canRead, businessName, timezone, refreshKey }: {
  base: string | null; canRead: boolean; businessName?: string; timezone?: string; refreshKey: number;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "forbidden" | "error">("loading");

  const load = useCallback(
    async (before: number | null) => {
      if (!base) return;
      const r = await fetch(`${base}/audit-events?limit=${HISTORY_PAGE}${before ? `&before_seq=${before}` : ""}`);
      if (r.status === 403) return setStatus("forbidden");
      if (!r.ok) return setStatus("error");
      const page = (await r.json()) as Page;
      setEvents((prev) => (before ? [...prev, ...page.events] : page.events));
      setNext(page.next_before_seq);
      setStatus("ready");
    },
    [base],
  );

  useEffect(() => {
    if (!base) return;
    if (canRead) load(null);
    else setStatus("forbidden");
  }, [base, canRead, load, refreshKey]);

  const fmt = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: timezone,
  });

  return (
        <section className="card" aria-labelledby="history-title">
          <div className="card-head">
            <h2 id="history-title" className="section-title">Activity history</h2>
            {status === "ready" && (
              <button type="button" className="btn btn-ghost btn-small" onClick={() => load(null)}>Refresh</button>
            )}
          </div>
          <p className="muted small">
            Append-only record for {businessName}. Times in {timezone ?? "your local time"}. Account numbers
            and document content are redacted before storage.
          </p>

          {status === "loading" && <p className="muted">Loading…</p>}
          {status === "forbidden" && (
            <p className="badge badge-info" role="status">
              Only owners and bookkeepers can view the audit history.
            </p>
          )}
          {status === "error" && <p className="badge badge-danger" role="alert">Could not load the audit history.</p>}
          {status === "ready" && events.length === 0 && <p className="muted">No events recorded yet.</p>}

          {status === "ready" && events.length > 0 && (
            <ol className="timeline">
              {events.map((e) => {
                const [cls, label] = OUTCOME_BADGE[e.outcome];
                return (
                  <li key={e.id} className="timeline-item">
                    <span className={`timeline-dot dot-${e.outcome}`} aria-hidden="true" />
                    <div className="timeline-body">
                      <div className="timeline-line">
                        <span className="timeline-text">{describe(e)}</span>
                        <span className={`badge ${cls}`}>{label}</span>
                      </div>
                      <div className="muted small">
                        <time dateTime={e.occurred_at}>{fmt.format(new Date(e.occurred_at))}</time>
                        {" · "}
                        {e.actor_name ?? "Unknown user"}
                        {e.actor_role && ` (${ROLE_LABELS[e.actor_role]})`}
                        {" · "}
                        <code title={e.event_hash}>#{e.seq}</code>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
          {status === "ready" && next && (
            <button type="button" className="btn btn-ghost" onClick={() => load(next)}>Load older events</button>
          )}
        </section>
  );
}

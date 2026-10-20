import { useCallback, useEffect, useState } from "react";
import { ROLE_LABELS, useSession, type Membership } from "../auth/session";

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
    case "alerts.run:success":
      return `Checked alerts: ${d.created} new, ${d.updated} changed, ${d.resolved} cleared`;
    case "alerts.run:denied":
      return "Tried to run the alert check without permission";
    case "outlook.run:denied":
      return "Tried to calculate a cash outlook without permission";
    case "cash.manage:denied":
      return "Tried to change cash records without permission";
    case "account.relabel:not_found":
      return "Tried to rename an account that is not in this business";
    default:
      return `${e.action} (${e.outcome})`;
  }
}

export function AuditPage() {
  const { active } = useSession();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [timezone, setTimezone] = useState<string | undefined>(undefined);
  const [status, setStatus] = useState<"loading" | "ready" | "forbidden" | "error">("loading");

  const base = active ? `/api/businesses/${active.business_id}` : null;
  const canRead = active?.allowed_actions.includes("audit.read") ?? false;

  const load = useCallback(
    async (before: number | null) => {
      if (!base) return;
      const r = await fetch(`${base}/audit-events?limit=25${before ? `&before_seq=${before}` : ""}`);
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
    setStatus("loading");
    if (!base) return;
    fetch(base)
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { timezone?: string } | null) => setTimezone(b?.timezone));
    if (canRead) load(null);
    else setStatus("forbidden");
  }, [base, canRead, load]);

  const fmt = new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: timezone,
  });

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Approvals &amp; Audit</h1>
          <p className="page-sub">Owner-controlled actions with a complete record of decisions</p>
        </div>
      </div>

      <div className="split">
        <section className="card" aria-labelledby="inbox-title">
          <h2 id="inbox-title" className="card-title">Approval inbox</h2>
          <span className="badge badge-info">Unavailable</span>
          <p className="muted">
            Approvals are not built yet (Prompt 15). Nothing here can approve or execute an action.
          </p>
        </section>

        <section className="card" aria-labelledby="history-title">
          <div className="card-head">
            <h2 id="history-title" className="card-title">Activity history</h2>
            {status === "ready" && (
              <button type="button" className="btn btn-ghost btn-small" onClick={() => load(null)}>Refresh</button>
            )}
          </div>
          <p className="muted small">
            Append-only record for {active?.business_name}. Times in {timezone ?? "your local time"}. Account numbers
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
      </div>
    </>
  );
}

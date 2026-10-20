export type ProposalState = "pending" | "approved" | "executable" | "rejected" | "withdrawn" | "expired";
export type Check = { code: string; ok: boolean; detail: string };
export type Decision = {
  event: "approved" | "rejected" | "withdrawn" | "executable"; actor: string; actor_role: string; at: string; reason: string | null;
  payload_sha256: string; reauthenticated: boolean; verification: { method: string; method_label?: string; reference: string; checked_on: string } | null;
  checks: Check[] | null;
};
export type Proposal = {
  id: string; kind: "invoice_reminder" | "supplier_payment" | "financing_application" | "funding_packet_share"; title: string; tier: "T2" | "T3";
  required_role: string; second_reviewer_required: boolean; policy_version: string; gates: string[];
  payload: Record<string, unknown>; payload_sha256: string; integrity: { payload_hash_matches: boolean };
  proposed_by: { id: string; name: string; role: string }; created_at: string; expires_at: string;
  state: ProposalState; expired_after_approval: boolean; decisions: Decision[];
  fraud_holds: { flag_id: string; rule: string; title: string }[];
  can: { approve: boolean; reject: boolean; withdraw: boolean; release: boolean };
  execution: ExecutionSummary | null; executed: boolean;
};
// Set by the sandbox executor once an executable proposal was sent to its mock adapter.
export type ExecutionSummary = {
  execution_id: string; adapter: string; outcome: "succeeded" | "rejected" | "failed" | "dispatched"; reference: string | null; completed_at: string | null;
};
export type Inbox = { items: Proposal[]; counts: Record<ProposalState, number> };
export type Policy = { version: string; verification_methods: Record<string, string>; tiers: Record<string, { max_hours: number; default_hours: number; gates: string[] }> };

// Status line under each inbox item (images/08-approvals-and-audit.png): colour plus words, never colour alone.
export function statusLine(p: Proposal): { text: string; cls: string } {
  switch (p.state) {
    case "pending":
      if (!p.integrity.payload_hash_matches) return { text: "On hold: integrity check failed", cls: "text-danger" };
      if (p.fraud_holds.length) return { text: `On hold in Fraud Monitor: ${p.fraud_holds[0].title}`, cls: "text-danger" };
      return p.tier === "T3"
        ? { text: p.second_reviewer_required ? "Owner re-auth and second reviewer required" : "Owner re-auth required", cls: "text-danger" }
        : { text: "Owner approval required", cls: "text-warning" };
    case "approved":
      if (p.fraud_holds.length) return { text: `Approved, now on hold in Fraud Monitor: ${p.fraud_holds[0].title}`, cls: "text-danger" };
      return { text: "Approved: release check needed", cls: "text-info" };
    case "executable":
      switch (p.execution?.outcome) {
        case "succeeded": return { text: `Executed in sandbox: ${p.execution.reference}`, cls: "text-success" };
        case "rejected": return { text: "Refused by the sandbox: final outcome", cls: "text-danger" };
        case "failed":
        case "dispatched": return { text: "Sandbox gave no answer: retry with the same key", cls: "text-warning" };
        default:
          if (p.fraud_holds.length) return { text: `Released, now on hold in Fraud Monitor: ${p.fraud_holds[0].title}`, cls: "text-danger" };
          return { text: "Executable: ready to run in the sandbox", cls: "text-info" };
      }
    case "expired":
      return { text: p.expired_after_approval ? "Approval expired before release" : "Expired without a decision", cls: "text-danger" };
    case "rejected":
      return { text: "Rejected by the owner", cls: "text-muted" };
    case "withdrawn":
      return { text: "Withdrawn by the proposer", cls: "text-muted" };
  }
}

export const STATE_BADGE: Record<ProposalState, [string, string]> = {
  pending: ["badge-warning", "Pending"],
  approved: ["badge-info", "Approved"],
  executable: ["badge-success", "Executable"],
  rejected: ["badge-muted", "Rejected"],
  withdrawn: ["badge-muted", "Withdrawn"],
  expired: ["badge-danger", "Expired"],
};

export const CHECK_LABELS: Record<string, string> = {
  releaser_role: "You are still an owner",
  approved: "Owner approval recorded",
  approver_role: "Approver is still an owner",
  second_reviewer: "Second reviewer",
  not_expired: "Not expired",
  payload_integrity: "Payload integrity (hash)",
  t3_gates: "Re-authentication and verification",
  fraud_hold: "No Fraud Monitor hold",
  records_current: "Records still match",
  released: "Approved and released",
  business_scope: "Business scope",
  adapter: "Sandbox adapter",
  destination_on_file: "Payee destination on file",
  idempotency_key: "Idempotency key",
};

// A calendar date (YYYY-MM-DD) in the business timezone, so the form and the server agree on "today".
export function isoDateIn(timezone: string | undefined, d: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

// Puts each server error under the field it belongs to; anything else becomes the form-level message.
// Proposal errors arrive as "<kind>.<field>" because the request body is a union by kind.
export function placeErrors(errors: Record<string, string>, fields: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [path, msg] of Object.entries(errors)) {
    const key = path.replace(/^(invoice_reminder|supplier_payment|financing_application|funding_packet_share)\./, "");
    if (key !== "_form" && fields.some((f) => key === f || key.startsWith(f + "."))) out[key] ??= msg;
    else out._form ??= msg;
  }
  return out;
}

// No long digit runs (account, card, or ID numbers) and no links; mirrors the server rules for early feedback.
export function textProblem(v: string, min: number, max: number, noLinks = false): string | undefined {
  const t = v.trim().replace(/\s+/g, " ");
  if (t.length < min) return `Enter at least ${min} characters.`;
  if (t.length > max) return `Use at most ${max} characters.`;
  if (/^[=+\-@]/.test(t)) return "Must not look like a spreadsheet formula.";
  const withoutDates = t.replace(/\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b/g, " ");  // 2026-10-05 is a date, not an account number
  if (/\d{8}/.test(withoutDates.replace(/[\s-]/g, ""))) return "Must not contain an account, card, or ID number.";
  if (noLinks && /https?:\/\/|www\./i.test(t)) return "Must not contain links.";
  return undefined;
}

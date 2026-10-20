import type { Check } from "../approvals/types";

export type LogEvent = "blocked" | "dispatched" | "succeeded" | "rejected" | "failed" | "replayed";

export type LogEntry = {
  id: string; seq: number; event: LogEvent; proposal_id: string; kind: string; tier: string; execution_id: string | null;
  adapter: string | null; adapter_label: string | null; idempotency_key: string; actor: string; actor_role: string; at: string;
  request: Record<string, unknown> | null; result: Record<string, unknown> | null; checks: Check[] | null;
  http_status: number | null; reference: string | null;
};

export type Execution = {
  id: string; proposal_id: string; adapter: string; adapter_label: string; endpoint: string; idempotency_key: string;
  payload_sha256: string; request_sha256: string; created_by: string; created_at: string;
  outcome: "succeeded" | "rejected" | "failed" | "dispatched"; final: boolean; reference: string | null;
  result: Record<string, unknown> | null; request: Record<string, unknown> | null; attempts: number; replays: number;
  completed_at: string | null; log: LogEntry[]; sandbox: true; replayed?: boolean;
};

export type AdapterInfo = { code: string; label: string; endpoint: string; proposal_kind: string; available: boolean; note: string };
export type Adapters = { mode: "sandbox"; credentials: "none"; base: string; adapters: AdapterInfo[] };

// Colour plus words for each log event; never colour alone.
export const EVENT_BADGE: Record<LogEvent, [string, string]> = {
  succeeded: ["badge-success", "Succeeded"],
  replayed: ["badge-info", "Replayed"],
  dispatched: ["badge-info", "Sent to sandbox"],
  blocked: ["badge-warning", "Blocked"],
  rejected: ["badge-danger", "Refused by sandbox"],
  failed: ["badge-danger", "No answer"],
};

export const KIND_LABEL: Record<string, string> = {
  invoice_reminder: "Invoice reminder",
  supplier_payment: "Supplier payment",
  financing_application: "Financing application",
  funding_packet_share: "Funding packet share",
};

// A new idempotency key for one proposal. It is kept for the browser tab, so pressing Run again after a lost answer
// sends the same key and the server returns the one stored outcome.
export function keyFor(proposalId: string): string {
  const slot = `sandbox-key:${proposalId}`;
  try {
    const saved = sessionStorage.getItem(slot);
    if (saved) return saved;
  } catch { /* storage may be unavailable; a fresh key is still safe */ }
  const fresh = "run-" + (typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join(""));
  try { sessionStorage.setItem(slot, fresh); } catch { /* ignore */ }
  return fresh;
}

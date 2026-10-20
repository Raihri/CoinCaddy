export type FlagState = "open" | "deferred" | "confirmed" | "cleared";
export type Rule = "duplicate_payment" | "unusual_payee_amount" | "split_near_limit" | "lookalike_payee" | "destination_change" | "paid_without_settlement";

export type RecordSnap = {
  type: "ledger_transaction" | "action_proposal" | "allocation" | "payee_destination" | "obligation" | "party";
  id: string; date?: string; payee?: string; amount_minor?: number; currency?: string; reference?: string | null;
  account_id?: string; account?: string; status?: string; obligation_id?: string;
  kind?: string; institution?: string; masked?: string; change_source?: string; direction?: string;
};
export type Hold = { proposal_id: string; supplier: string; document_number: string; amount_minor: number; currency: string; from_account: string;
  proposed_by_name: string; held_now?: boolean };
export type Review = { decision: "confirmed" | "rejected" | "deferred"; reason: string; defer_until: string | null; reviewer: string; reviewer_role: string; at: string;
  verification: { method: string; method_label?: string; contact_source: string; checked_on: string } | null };
export type Outcome = { label: string; lowest_expected: { date: string; amount_minor: number }; lowest_cautious: { date: string; amount_minor: number };
  below_reserve: boolean };
export type CashOutcomes =
  | { available: true; as_of: string; calc_version: string; inputs_sha256: string; currency: string; reserve_minor: number | null;
      materiality_threshold_minor: number; assumptions: string[]; outcomes: [Outcome, Outcome]; difference_minor: number }
  | { available: false; reason: string };

export type Flag = {
  id: string; rule: Rule; rules_version: string; title: string; severity: "high" | "medium"; reason: string; currency: string; amount_minor: number;
  state: FlagState; review_due: boolean; subject_refs: { type: string; id: string }[]; compared_refs: { type: string; id: string }[];
  evidence: { subjects: RecordSnap[]; compared: RecordSnap[]; [k: string]: unknown }; holds: Hold[]; holding: boolean; created_at: string; verdict: string;
};
export type FlagDetail = Flag & {
  reviews: Review[]; cash_outcomes: CashOutcomes; outcome_labels: [string, string];
  can: { confirm: boolean; defer: boolean; reject: boolean; reject_blocked_reason: string | null; reject_needs: string[]; verification_required: boolean };
};
export type TxnRow = { type: "ledger_transaction" | "action_proposal"; id: string; date: string; payee: string | null; amount_minor: number; currency: string;
  reference: string | null; account_id: string; account: string; status: "posted" | "review" | "on_hold" | "proposed"; flag_ids: string[]; proposal_state?: string };
export type Overview = {
  rules_version: string; counts: Record<FlagState, number>; unresolved: number;
  held_payments: { id: string; payee: string; amount_minor: number; currency: string; reference: string; flags: { flag_id: string; rule: Rule; title: string }[] }[];
  last_scan: { at: string; as_of: string; trigger: string; by: string; flags_created: number } | null;
};
export type RulesInfo = {
  version: string; rules: { rule: Rule; title: string; severity: string; description: string }[];
  verification_methods: Record<string, string>; change_sources: Record<string, string>; max_defer_days: number; note: string;
};
export type Destination = { id: string; kind: "bank" | "mobile_wallet"; institution: string; masked: string; change_source: string; note: string | null;
  recorded_by: string; recorded_at: string; date: string };
export type PayeeDestinations = { parties: { party_id: string; name: string; current: Destination | null; history: Destination[] }[];
  change_sources: Record<string, string>; note: string };

// Badge text for each rule, as in images/07-fraud-monitor.png ("POSSIBLE DUPLICATE").
export const RULE_BADGE: Record<Rule, string> = {
  duplicate_payment: "Possible duplicate",
  unusual_payee_amount: "Unusual amount",
  split_near_limit: "Split near limit",
  lookalike_payee: "Lookalike payee",
  destination_change: "Destination changed",
  paid_without_settlement: "No settlement",
};

export const STATE_BADGE: Record<FlagState, [string, string]> = {
  open: ["badge-danger", "Needs review"],
  deferred: ["badge-warning", "Deferred"],
  confirmed: ["badge-danger", "Confirmed suspicious"],
  cleared: ["badge-success", "Cleared"],
};

export const TXN_BADGE: Record<TxnRow["status"], [string, string]> = {
  on_hold: ["badge-danger", "On hold"],
  review: ["badge-warning", "Review"],
  proposed: ["badge-info", "Proposed"],
  posted: ["badge-success", "Posted"],
};

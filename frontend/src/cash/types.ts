export type CashTxn = {
  id: string; account_id: string; business_date: string; amount_minor: number; currency: string; description: string; reference: string | null;
};

export type Period = {
  from_anchor_id: string; to_anchor_id: string; from_date: string; to_date: string;
  opening_minor: number; movements_minor: number; movement_count: number; expected_closing_minor: number;
  closing_minor: number; residual_minor: number; coverage_gaps: { start: string; end: string }[];
  status: "balanced" | "balanced_incomplete" | "residual"; transaction_ids: string[];
};

export type AccountPosition = {
  account: { id: string; label: string; kind: string; currency: string };
  anchors: { id: string; anchor_date: string; balance_minor: number; source_kind: string; source_reference: string }[];
  coverage: { start: string; end: string }[];
  periods: Period[];
  restricted_minor: number;
  restrictions: { id: string; amount_minor: number; reason: string; effective_from: string; effective_to: string | null }[];
  latest_anchor: { id: string; anchor_date: string; balance_minor: number } | null;
  evidence_through: string | null;
  age_days: number | null;
  verified_balance_minor: number | null;
  usable_minor: number | null;
  uncovered_movements: { count: number; sum_minor: number; transaction_ids: string[] };
  freshness: "fresh" | "stale" | "no_anchor";
  issues: { code: string; message: string }[];
  status: "verified" | "stale" | "missing" | "needs_review";
};

export type TotalsEntry = { account_id: string; label: string; usable_minor: number | null; evidence_through: string | null };

export type Position = {
  as_of: string;
  calc_version: string;
  fresh_max_age_days: number;
  accounts: AccountPosition[];
  totals: { currency: string; spendable_minor: number; restricted_minor: number; included: TotalsEntry[]; stale: TotalsEntry[]; missing: TotalsEntry[] }[];
  transfers: { id: string; status: "matched" | "unmatched"; amount_minor: number | null; currency: string | null; out: CashTxn | null; in: CashTxn | null;
               note: string | null; missing_side: "incoming" | "outgoing" | null }[];
  transfer_suggestions: { out_txn_id: string; in_txn_id: string; amount_minor: number; currency: string }[];
};

export const ACCOUNT_STATUS: Record<AccountPosition["status"], [string, string]> = {
  verified: ["badge-success", "Verified"],
  stale: ["badge-warning", "Stale"],
  missing: ["badge-danger", "Missing balance"],
  needs_review: ["badge-warning", "Needs review"],
};

export const ACCOUNT_STATUS_HELP: Record<AccountPosition["status"], string> = {
  verified: "Recent balance evidence is available. Restrictions and movements after its verified date are still accounted for.",
  stale: "The last balance is older than the freshness limit, so it is excluded from Spendable now until updated.",
  missing: "There is no verified balance yet, so this account is excluded from Spendable now.",
  needs_review: "The balance needs a review. Check the reconciliation result, coverage gaps, and uncovered movements below.",
};

export const PERIOD_STATUS: Record<Period["status"], [string, string]> = {
  balanced: ["badge-success", "Balanced"],
  balanced_incomplete: ["badge-warning", "Zero residual, coverage incomplete"],
  residual: ["badge-danger", "Does not reconcile"],
};

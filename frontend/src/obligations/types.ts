export type Obligation = {
  id: string;
  direction: "receivable" | "payable";
  party: { id: string; name: string };
  document_number: string;
  issue_date: string;
  due_date: string;
  amount_minor: number;
  allocated_minor: number;
  outstanding_minor: number;
  currency: string;
  category: string;
  description: string | null;
  source_reference: string;
  settlement_account: { id: string; label: string } | null;
  disputed: boolean;
  dispute_reason: string | null;
  cancelled: boolean;
  cancel_reason: string | null;
  status: "open" | "partially_settled" | "settled" | "cancelled";
  overdue: boolean;
  days_overdue: number;
};

export type Allocation = {
  id: string; kind: "payment" | "reversal"; amount_minor: number; currency: string; allocation_date: string;
  note: string | null; created_by: string; reverses_id: string | null; reversed: boolean;
  ledger_txn: { id: string; business_date: string; description: string; amount_minor: number } | null;
};

export type DuesSummary = {
  as_of: string;
  upcoming_days: number;
  customer_dues: { currency: string; due_later: number; due_this_week: number; overdue_1_30: number; overdue_31_plus: number;
                   disputed: number; total_outstanding: number; overdue_count: number; customers_overdue: number }[];
  upcoming_payables: { currency: string; total: number; overdue_total: number;
                       items: (Pick<Obligation, "id" | "party" | "document_number" | "due_date" | "outstanding_minor" | "currency" | "category" | "overdue" | "days_overdue" | "disputed">)[] }[];
};

export const CATEGORY_LABELS: Record<string, string> = {
  sales: "Sales", other_income: "Other income", inventory_suppliers: "Inventory / suppliers", rent: "Rent", utilities: "Utilities",
  salaries: "Salaries", transport: "Transport", marketing: "Marketing", loan_repayment: "Loan repayment", tax: "Tax", other_expense: "Other expense",
};

export const RECEIVABLE_CATEGORIES = ["sales", "other_income"];
export const PAYABLE_CATEGORIES = ["inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing", "loan_repayment", "tax", "other_expense"];

export const OB_STATUS: Record<Obligation["status"], [string, string]> = {
  open: ["badge-info", "Open"],
  partially_settled: ["badge-warning", "Partly paid"],
  settled: ["badge-success", "Paid"],
  cancelled: ["badge-muted", "Cancelled"],
};

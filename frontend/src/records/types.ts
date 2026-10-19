export type Mapping = {
  date_column: number | null;
  date_format: string | null;
  description_column: number | null;
  amount_mode: "signed" | "debit_credit";
  amount_column: number | null;
  debit_column: number | null;
  credit_column: number | null;
  counterparty_column: number | null;
  reference_column: number | null;
  currency_column: number | null;
};

export type Counts = {
  total: number; valid: number; error: number; duplicates: number; pending: number; included: number; excluded: number;
};

export type ImportBatch = {
  id: string;
  account: { id: string; label: string; currency: string };
  source_name: string;
  file_type: "csv" | "xlsx";
  size_bytes: number;
  sha256: string;
  parser_version: string;
  status: "needs_mapping" | "staged" | "posted" | "discarded";
  revision: number;
  row_count: number;
  headers: string[];
  column_profile: { kinds: string[]; sample: string | null; date_formats: string[] }[];
  mapping: Mapping | null;
  suggested_mapping: Mapping & { date_format_ambiguous: boolean };
  uploaded_by: string;
  created_at: string;
  posted_at: string | null;
  posted_count: number | null;
  counts: Counts;
  notice: string;
  duplicate_upload?: boolean;
  commit?: { posted_count: number; replayed: boolean };
};

export type StagedRow = {
  row_number: number;
  raw: ({ t: string; v: string } | null)[];
  status: "unmapped" | "valid" | "error";
  errors: { field: string; message: string }[];
  business_date: string | null;
  amount_minor: number | null;
  currency: string | null;
  description: string | null;
  counterparty: string | null;
  reference: string | null;
  decision: "include" | "exclude" | "pending";
  duplicate: null | {
    kind: "in_file" | "in_ledger";
    row_number: number | null;
    transaction: null | { id: string; business_date: string; amount_minor: number; description: string; reference: string | null };
  };
};

export const STATUS_BADGE: Record<ImportBatch["status"], [string, string]> = {
  needs_mapping: ["badge-warning", "Needs column mapping"],
  staged: ["badge-info", "Staged for review"],
  posted: ["badge-success", "Posted"],
  discarded: ["badge-muted", "Discarded"],
};

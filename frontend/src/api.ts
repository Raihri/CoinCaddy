import { formatDate } from "./region/format";
import { readErrors, type FieldErrors } from "./settings/apiErrors";

export type ApiResult<T> = { ok: true; data: T } | { ok: false; status: number; errors: FieldErrors; message: string };

// JSON request helper: returns parsed data, or field/form errors in the same shape the forms already use.
export async function api<T>(url: string, method = "GET", body?: unknown): Promise<ApiResult<T>> {
  const r = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.ok) return { ok: true, data: (await r.json()) as T };
  const errors = await readErrors(r);
  return { ok: false, status: r.status, errors, message: errors._form ?? Object.values(errors)[0] ?? `Request failed (HTTP ${r.status}).` };
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return formatDate(iso);
}

export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

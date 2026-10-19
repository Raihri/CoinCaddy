// Turn FastAPI 422/409/403 responses into field-level and form-level messages.
export type FieldErrors = Record<string, string>;

type Detail = { loc?: (string | number)[]; msg?: string };

export async function readErrors(r: Response): Promise<FieldErrors> {
  const body = (await r.json().catch(() => ({}))) as { detail?: string | Detail[] | { message?: string } };
  if (typeof body.detail === "string") return { _form: body.detail };
  if (body.detail && !Array.isArray(body.detail) && body.detail.message) return { _form: body.detail.message };
  if (r.status === 422 && Array.isArray(body.detail)) {
    const out: FieldErrors = {};
    for (const d of body.detail) {
      const path = (d.loc ?? []).filter((p) => p !== "body").join(".");
      const msg = (d.msg ?? "Invalid value").replace(/^Value error, /, "");
      out[path || "_form"] ??= msg;
    }
    return out;
  }
  if (r.status === 403) return { _form: "Your role does not allow this change." };
  return { _form: `Request failed (HTTP ${r.status}).` };
}

export function errorFor(errors: FieldErrors, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const hit = Object.entries(errors).find(([path]) => path === k || path.startsWith(k + "."));
    if (hit) return hit[1];
  }
  return undefined;
}

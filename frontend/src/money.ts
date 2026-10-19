// Exact conversion between typed major-unit amounts and integer minor units. No floating point:
// "80,000.50" BDT -> 8000050. The server re-validates everything; this only gives early feedback.

export type ParseResult = { ok: true; minor: number } | { ok: false; error: string };

export function parseMajorToMinor(input: string, exponent: number, maxMinor: number): ParseResult {
  const cleaned = input.replace(/[,\s৳]/g, "");
  if (cleaned === "") return { ok: false, error: "Enter an amount." };
  if (cleaned.startsWith("-")) return { ok: false, error: "The amount cannot be negative." };
  const m = /^(\d+)(?:\.(\d+))?$/.exec(cleaned);
  if (!m) return { ok: false, error: "Use digits only, for example 80,000.00." };
  const frac = m[2] ?? "";
  if (frac.length > exponent) {
    return { ok: false, error: exponent === 0 ? "This currency has no decimal places." : `Use at most ${exponent} decimal places.` };
  }
  const minor = BigInt(m[1]) * 10n ** BigInt(exponent) + BigInt(frac.padEnd(exponent, "0") || "0");
  if (minor > BigInt(maxMinor)) return { ok: false, error: "The amount is too large." };
  return { ok: true, minor: Number(minor) };
}

function group(whole: string, lakh: boolean): string {
  if (!lakh) return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  // South Asian grouping used for BDT: 1,00,000.
  if (whole.length <= 3) return whole;
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return `${rest},${last3}`;
}

export function minorToMajorString(minor: number, exponent: number): string {
  const s = Math.abs(minor).toString().padStart(exponent + 1, "0");
  const whole = exponent ? s.slice(0, -exponent) : s;
  const frac = exponent ? s.slice(-exponent) : "";
  return `${minor < 0 ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

export function formatMoney(minor: number, currency: string, exponent: number): string {
  const plain = minorToMajorString(Math.abs(minor), exponent);
  const [whole, frac] = plain.split(".");
  const body = group(whole, currency === "BDT") + (frac ? "." + frac : "");
  const sign = minor < 0 ? "-" : "";
  return currency === "BDT" ? `${sign}৳ ${body}` : `${sign}${currency} ${body}`;
}

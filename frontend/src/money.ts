// Exact conversion between typed major-unit amounts and integer minor units. No floating point:
// "80,000.50" BDT -> 8000050. The server re-validates everything; this only gives early feedback.

export type ParseResult = { ok: true; minor: number } | { ok: false; error: string };

export function parseMajorToMinor(input: string, exponent: number, maxMinor: number, allowNegative = false): ParseResult {
  let cleaned = input.replace(/[,\s৳]/g, "");
  if (cleaned === "") return { ok: false, error: "Enter an amount." };
  if (cleaned.startsWith("-")) {
    if (!allowNegative) return { ok: false, error: "The amount cannot be negative." };
    const inner = parseMajorToMinor(cleaned.slice(1), exponent, maxMinor);
    return inner.ok ? { ok: true, minor: -inner.minor } : inner;
  }
  cleaned = cleaned.replace(/^\+/, "");
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

export function minorToMajorString(minor: number, exponent: number): string {
  const s = Math.abs(minor).toString().padStart(exponent + 1, "0");
  const whole = exponent ? s.slice(0, -exponent) : s;
  const frac = exponent ? s.slice(-exponent) : "";
  return `${minor < 0 ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

// Display follows the active Region Pack (see region/format.ts); the stored value is never changed.
export { formatMoney } from "./region/format";

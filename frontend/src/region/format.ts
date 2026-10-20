// Presentation of money and dates for the active Region Pack. This only changes how a stored value is written on screen:
// it never converts between currencies and never alters the integer minor units it is given.

export type RegionFormat = {
  id: string;
  version: string;
  currency: string;
  number_format: { grouping: "lakh" | "thousands"; group_separator: string; decimal_separator: string };
  currency_display: { symbol: string; position: "prefix" | "suffix" };
  date_display: "DD MMM YYYY" | "DD/MM/YYYY" | "YYYY-MM-DD";
  labels: Record<string, string>;
};

let active: RegionFormat | null = null;

export function setRegionFormat(f: RegionFormat | null): void {
  active = f;
}

export function groupDigits(whole: string, grouping: "lakh" | "thousands", sep: string): string {
  if (grouping === "thousands") return whole.replace(/\B(?=(\d{3})+(?!\d))/g, sep);
  // South Asian grouping: 12,34,567 (last three digits, then pairs).
  if (whole.length <= 3) return whole;
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, sep);
  return `${rest}${sep}${whole.slice(-3)}`;
}

/** Writes integer minor units for display. A pack's symbol applies only to the pack's own currency; any other currency keeps its ISO code. */
export function formatMoneyWith(f: RegionFormat | null, minor: number, currency: string, exponent: number): string {
  const s = Math.abs(minor).toString().padStart(exponent + 1, "0");
  const whole = exponent ? s.slice(0, -exponent) : s;
  const frac = exponent ? s.slice(-exponent) : "";
  const sign = minor < 0 ? "-" : "";
  if (f === null) {
    // No pack applies: the app's built-in display (lakh grouping and the Taka sign for BDT, thousands grouping otherwise).
    const body = groupDigits(whole, currency === "BDT" ? "lakh" : "thousands", ",") + (frac ? "." + frac : "");
    return currency === "BDT" ? `${sign}৳ ${body}` : `${sign}${currency} ${body}`;
  }
  const nf = f.number_format;
  const body = groupDigits(whole, nf.grouping, nf.group_separator) + (frac ? nf.decimal_separator + frac : "");
  if (currency !== f.currency) return `${sign}${currency} ${body}`;
  const sym = f.currency_display.symbol;
  return f.currency_display.position === "prefix" ? `${sign}${sym} ${body}` : `${sign}${body} ${sym}`;
}

export function formatMoney(minor: number, currency: string, exponent: number): string {
  return formatMoneyWith(active, minor, currency, exponent);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatDateWith(f: RegionFormat | null, iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const [, y, mo, d] = m;
  const month = Number(mo);
  if (month < 1 || month > 12) return iso;
  switch (f?.date_display ?? "DD MMM YYYY") {
    case "DD/MM/YYYY": return `${d}/${mo}/${y}`;
    case "YYYY-MM-DD": return `${y}-${mo}-${d}`;
    default: return `${d} ${MONTHS[month - 1]} ${y}`;
  }
}

export function formatDate(iso: string): string {
  return formatDateWith(active, iso);
}

export function regionLabel(key: string, fallback: string): string {
  return active?.labels[key] ?? fallback;
}

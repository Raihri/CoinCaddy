import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { BarPairChart } from "../charts/Charts";
import { formatMoney } from "../money";
import { CATEGORY_LABELS } from "../obligations/types";
import { exponentOf } from "../settings/fields";

type Cat = { category: string; amount_minor: number; transaction_ids: string[] };
type Month = {
  month: string; currency: string; operating_receipts_minor: number; operating_payments_minor: number; net_operating_cash_minor: number;
  receipt_categories: Cat[]; payment_categories: Cat[];
  margin: { basis_points: number | null; available: boolean; reason: string | null };
  excluded: Record<string, { in_minor: number; out_minor: number; transaction_ids: string[] }>;
  unclassified: { count: number; in_minor: number; out_minor: number; transaction_ids: string[] };
  complete: boolean; transaction_count: number;
};
type Health = {
  calc_version: string; as_of: string; notice: string; currencies: string[]; summary: Month;
  previous: Pick<Month, "month" | "operating_receipts_minor" | "operating_payments_minor" | "net_operating_cash_minor" | "complete">;
  trend: Pick<Month, "month" | "operating_receipts_minor" | "operating_payments_minor" | "net_operating_cash_minor" | "complete">[];
  what_changed: string;
  coverage: { account_id: string; label: string; has_movements: boolean; uncovered_days: number }[];
  customer_dues: { due_this_week: number; overdue_1_30: number; overdue_31_plus: number; due_later: number; disputed: number;
                   total_outstanding: number; customers_overdue: number; overdue_count: number } | null;
  upcoming_payables: { total: number; items: { id: string; party: { name: string }; document_number: string; due_date: string; outstanding_minor: number; overdue: boolean }[] } | null;
};
type Drill = { kind: string; category: string | null; total_minor: number;
               transactions: { id: string; business_date: string; amount_minor: number; description: string; reference: string | null; account_label: string; source_row_number: number }[] };

const EXCLUDED_LABELS: Record<string, string> = {
  own_transfer: "Own-account transfers", financing_inflow: "Financing received", financing_outflow: "Financing repaid",
  owner_contribution: "Owner contributions", owner_drawing: "Owner drawings", other_non_operating: "Other non-operating",
};
const KIND_OPTIONS: [string, string][] = [
  ["operating_receipt", "Operating receipt"], ["operating_payment", "Operating payment"], ["financing_inflow", "Financing received"],
  ["financing_outflow", "Financing repaid"], ["owner_contribution", "Owner contribution"], ["owner_drawing", "Owner drawing"],
  ["other_non_operating", "Other non-operating"],
];

export function FinancialHealthPage() {
  const { active, profile, options } = useSession();
  const [month, setMonth] = useState<string>("");
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drill, setDrill] = useState<Drill | null>(null);
  const base = active ? `/api/businesses/${active.business_id}` : "";
  const currency = profile?.base_currency ?? "BDT";
  const exp = exponentOf(options, currency);
  const money = (m: number) => formatMoney(m, currency, exp);
  const short = (m: number) => (m === 0 ? "0" : `${Math.round(m / 10 ** exp / 1000).toLocaleString("en-IN")}k`);

  const load = useCallback(async () => {
    if (!base) return;
    const r = await api<Health>(`${base}/financial-health${month ? `?month=${month}` : ""}`);
    if (r.ok) { setData(r.data); setError(null); if (!month) setMonth(r.data.summary.month); } else setError(r.message);
  }, [base, month]);
  useEffect(() => { load(); setDrill(null); }, [load]);

  const open = async (kind: string, category?: string) => {
    const r = await api<Drill>(`${base}/financial-health/transactions?month=${data!.summary.month}&kind=${kind}${category ? `&category=${category}` : ""}`);
    if (r.ok) { setDrill(r.data); setTimeout(() => document.getElementById("drill")?.scrollIntoView({ behavior: "smooth" }), 50); }
  };

  if (error) return <p className="badge badge-danger">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const s = data.summary;
  const pct = (bp: number) => `${bp < 0 ? "-" : ""}${Math.floor(Math.abs(bp) / 100)}.${String(Math.abs(bp) % 100).padStart(2, "0")}%`;
  const change = (now: number, before: number) => {
    if (before === 0) return now === 0 ? "No change" : "New this month";
    const d = Math.round(((now - before) * 1000) / Math.abs(before)) / 10;
    return `${d >= 0 ? "Up" : "Down"} ${Math.abs(d)}% vs ${data.previous.month}`;
  };
  const dues = data.customer_dues;
  const overdue = dues ? dues.overdue_1_30 + dues.overdue_31_plus : 0;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Financial health</h1>
          <p className="page-sub">Plain-language performance with a link back to every source record</p>
        </div>
        <label className="field inline-field">
          <span>Month</span>
          <input type="month" value={month} max={data.as_of.slice(0, 7)} min="2000-01" onChange={(e) => e.target.value && setMonth(e.target.value)} />
        </label>
      </div>
      <p className="notice">{data.notice}</p>
      {!s.complete && (
        <p className="badge badge-warning block-badge" role="status">
          Incomplete: {s.unclassified.count} posted transaction(s) in {s.month} are not classified ({money(s.unclassified.in_minor)} in, {money(s.unclassified.out_minor)} out). Classify them below.
        </p>
      )}

      <div className="kpi-grid">
        <button type="button" className="card kpi kpi-button" onClick={() => open("operating_receipt")}>
          <span className="kpi-label">Operating receipts · {s.month}</span>
          <span className="kpi-value num">{money(s.operating_receipts_minor)}</span>
          <span className="badge badge-info">{change(s.operating_receipts_minor, data.previous.operating_receipts_minor)}</span>
        </button>
        <button type="button" className="card kpi kpi-button" onClick={() => open("operating_payment")}>
          <span className="kpi-label">Operating payments</span>
          <span className="kpi-value num">{money(s.operating_payments_minor)}</span>
          <span className="badge badge-info">{change(s.operating_payments_minor, data.previous.operating_payments_minor)}</span>
        </button>
        <section className="card kpi">
          <span className="kpi-label">Operating cash margin</span>
          <span className="kpi-value num">{s.margin.available ? pct(s.margin.basis_points!) : "Not available"}</span>
          <span className="muted small">{s.margin.available ? `Net ${money(s.net_operating_cash_minor)} ÷ receipts` : s.margin.reason}</span>
        </section>
        <Link to="/records/obligations" className="card kpi kpi-button">
          <span className="kpi-label">Overdue invoices</span>
          <span className="kpi-value num">{money(overdue)}</span>
          <span className={`badge ${overdue ? "badge-warning" : "badge-success"}`}>{dues?.customers_overdue ?? 0} customer(s){dues?.disputed ? ` · ${money(dues.disputed)} disputed` : ""}</span>
        </Link>
      </div>

      <div className="split split-wide">
        <section className="card" aria-labelledby="trend-title">
          <h2 id="trend-title" className="card-title">Operating receipts and payments</h2>
          <p className="muted small">Monthly, {currency} thousands. Months with unclassified records are incomplete.</p>
          <BarPairChart
            labels={data.trend.map((t) => new Date(t.month + "-01T00:00:00").toLocaleDateString("en-GB", { month: "short" }) + (t.complete ? "" : "*"))}
            series={[{ name: "Receipts", values: data.trend.map((t) => t.operating_receipts_minor) },
                     { name: "Payments", values: data.trend.map((t) => t.operating_payments_minor) }]}
            format={money} axisFormat={short} caption="Monthly operating receipts and payments" />
        </section>
        <div className="stack">
          <section className="card" aria-labelledby="changed-title">
            <h2 id="changed-title" className="card-title">What changed</h2>
            <p>{data.what_changed}</p>
            <button type="button" className="btn btn-ghost btn-small" onClick={() => open("operating_receipt")}>View source transactions</button>
          </section>
          <section className="card" aria-labelledby="fh-dues-title">
            <h2 id="fh-dues-title" className="card-title">Customer dues</h2>
            {dues ? (
              <dl className="lines">
                <div><dt>Due this week</dt><dd className="num">{money(dues.due_this_week)}</dd></div>
                <div><dt>Overdue 1–30 days</dt><dd className="num">{money(dues.overdue_1_30)}</dd></div>
                <div><dt>Overdue 30+ days</dt><dd className="num">{money(dues.overdue_31_plus)}</dd></div>
                <div><dt>Disputed</dt><dd className="num">{money(dues.disputed)}</dd></div>
              </dl>
            ) : <p className="muted">No customer dues.</p>}
          </section>
        </div>
      </div>

      <div className="split split-even">
        <section className="card" aria-labelledby="cats-title">
          <h2 id="cats-title" className="card-title">By category · {s.month}</h2>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Category</th><th className="right">Amount</th><th /></tr></thead>
              <tbody>
                {s.receipt_categories.map((c) => (
                  <tr key={"r" + c.category}><td>{CATEGORY_LABELS[c.category] ?? c.category} <span className="badge badge-success">receipt</span></td>
                    <td className="num right">{money(c.amount_minor)}</td>
                    <td><button type="button" className="btn btn-ghost btn-small" onClick={() => open("operating_receipt", c.category)}>{c.transaction_ids.length} records</button></td></tr>
                ))}
                {s.payment_categories.map((c) => (
                  <tr key={"p" + c.category}><td>{CATEGORY_LABELS[c.category] ?? c.category.replace(/_/g, " ")} <span className="badge badge-muted">payment</span></td>
                    <td className="num right">{money(c.amount_minor)}</td>
                    <td><button type="button" className="btn btn-ghost btn-small" onClick={() => open("operating_payment", c.category)}>{c.transaction_ids.length} records</button></td></tr>
                ))}
                {s.receipt_categories.length + s.payment_categories.length === 0 && <tr><td colSpan={3} className="muted">No classified operating records this month.</td></tr>}
                <tr className="total-row"><td>Net operating cash</td><td className="num right">{money(s.net_operating_cash_minor)}</td><td /></tr>
              </tbody>
            </table>
          </div>
        </section>
        <section className="card" aria-labelledby="excl-title">
          <h2 id="excl-title" className="card-title">Not counted as revenue or expense</h2>
          <ul className="movement-list">
            {Object.keys(s.excluded).length === 0 && <li className="muted">Nothing excluded this month.</li>}
            {Object.entries(s.excluded).map(([k, v]) => (
              <li key={k}><button type="button" className="movement movement-button" onClick={() => open(k)}>
                <span><strong>{EXCLUDED_LABELS[k] ?? k}</strong><span className="muted small block">{v.transaction_ids.length} records</span></span>
                <span className="num excluded-amounts"><span className="nowrap">in {money(v.in_minor)}</span> <span className="nowrap">out {money(v.out_minor)}</span></span>
              </button></li>
            ))}
          </ul>
          {data.coverage.some((c) => c.has_movements && c.uncovered_days > 0) && (
            <p className="excluded small">Source coverage is incomplete for {data.coverage.filter((c) => c.has_movements && c.uncovered_days > 0).map((c) => `${c.label} (${c.uncovered_days} days)`).join(", ")}; records may be missing.</p>
          )}
        </section>
      </div>

      {drill && (
        <section className="card" id="drill" aria-labelledby="drill-title">
          <div className="card-head">
            <h2 id="drill-title" className="card-title">Source records: {(EXCLUDED_LABELS[drill.kind] ?? KIND_OPTIONS.find((k) => k[0] === drill.kind)?.[1] ?? drill.kind)}{drill.category ? ` · ${CATEGORY_LABELS[drill.category] ?? drill.category}` : ""}</h2>
            <button type="button" className="btn btn-ghost btn-small" onClick={() => setDrill(null)}>Close</button>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Date</th><th>Account</th><th>Description</th><th>Ref</th><th className="right">Amount</th></tr></thead>
              <tbody>
                {drill.transactions.map((t) => (
                  <tr key={t.id}><td className="nowrap">{fmtDate(t.business_date)}</td><td>{t.account_label}</td><td className="wrap">{t.description}</td>
                    <td className="muted">{t.reference ?? "—"} · row {t.source_row_number}</td><td className={`num right ${t.amount_minor < 0 ? "neg" : ""}`}>{money(t.amount_minor)}</td></tr>
                ))}
                <tr className="total-row"><td colSpan={4}>Total (absolute)</td><td className="num right">{money(drill.total_minor)}</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      <ClassifyCard base={base} month={s.month} money={money} onDone={load} />
      <p className="muted small">Calculation {data.calc_version}, integer arithmetic in minor units on the server; no AI is used for any figure. As of {fmtDate(data.as_of)}.</p>
    </>
  );
}

type ClassItem = { id: string; business_date: string; amount_minor: number; description: string; account_label: string;
                   kind: string | null; category: string | null; suggestion: { kind: string; category: string | null; because: string } | null };

function ClassifyCard({ base, month, money, onDone }: { base: string; month: string; money: (m: number) => string; onDone: () => void }) {
  const { active } = useSession();
  const [items, setItems] = useState<ClassItem[]>([]);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);
  const canClassify = active?.allowed_actions.includes("ledger.classify") ?? false;
  const RC = ["sales", "other_income"];
  const PC = ["inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing", "fees_charges", "tax", "other_expense"];

  const load = useCallback(async () => {
    const r = await api<{ items: ClassItem[] }>(`${base}/classifications?month=${month}`);
    if (r.ok) {
      setItems(r.data.items);
      const init: Record<string, string> = {};
      for (const i of r.data.items) {
        const k = i.kind ?? i.suggestion?.kind, c = i.kind ? i.category : i.suggestion?.category;
        if (k && k !== "own_transfer") init[i.id] = c ? `${k}:${c}` : k;
      }
      setChoice(init);
    }
  }, [base, month]);
  useEffect(() => { load(); }, [load]);

  const options = (amount: number) => [
    ...(amount > 0 ? RC.map((c) => [`operating_receipt:${c}`, `Receipt · ${CATEGORY_LABELS[c] ?? c}`]) : PC.map((c) => [`operating_payment:${c}`, `Payment · ${CATEGORY_LABELS[c] ?? c.replace(/_/g, " ")}`])),
    ...KIND_OPTIONS.filter(([k]) => !k.startsWith("operating") && (k === "other_non_operating" || (amount > 0) === ["financing_inflow", "owner_contribution"].includes(k))),
  ];
  const save = async () => {
    const payload = items.filter((i) => i.kind !== "own_transfer" && choice[i.id] && choice[i.id] !== (i.category ? `${i.kind}:${i.category}` : i.kind))
      .map((i) => { const [kind, category] = choice[i.id].split(":"); return category ? { txn_id: i.id, kind, category } : { txn_id: i.id, kind }; });
    if (!payload.length) return setMessage({ kind: "info", text: "Nothing changed." });
    const r = await api(`${base}/classifications`, "POST", { items: payload });
    if (r.ok) { setMessage({ kind: "success", text: `Saved ${payload.length} classification(s).` }); load(); onDone(); }
    else setMessage({ kind: "danger", text: r.message });
  };
  if (items.length === 0) return null;
  return (
    <section className="card" aria-labelledby="classify-title">
      <div className="card-head">
        <h2 id="classify-title" className="card-title">Classify posted records · {month}</h2>
        {canClassify && <button type="button" className="btn btn-primary btn-small" onClick={save}>Save classifications</button>}
      </div>
      <p className="muted small">Suggestions come only from invoices and bills you linked to bank transactions. Nothing is saved until you press Save.</p>
      {message && <p className={`badge badge-${message.kind}`} role="status">{message.text}</p>}
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Date</th><th>Description</th><th className="right">Amount</th><th>Classification</th></tr></thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id}>
                <td className="nowrap">{fmtDate(i.business_date)}</td>
                <td className="wrap">{i.description} <span className="muted small block">{i.account_label}</span></td>
                <td className={`num right ${i.amount_minor < 0 ? "neg" : ""}`}>{money(i.amount_minor)}</td>
                <td>
                  {i.kind === "own_transfer" ? <span className="badge badge-muted">Own-account transfer</span>
                    : canClassify ? (
                      <span className="classify-cell">
                        <select className="select" aria-label={`Classification for ${i.description}`} value={choice[i.id] ?? ""}
                                onChange={(e) => setChoice({ ...choice, [i.id]: e.target.value })}>
                          <option value="">Not classified</option>
                          {options(i.amount_minor).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </select>
                        {!i.kind && i.suggestion && <span className="badge badge-info">Suggested: {i.suggestion.because}</span>}
                      </span>
                    ) : <span>{i.kind ? `${i.kind.replace(/_/g, " ")}${i.category ? ` · ${i.category.replace(/_/g, " ")}` : ""}` : <span className="badge badge-warning">Not classified</span>}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

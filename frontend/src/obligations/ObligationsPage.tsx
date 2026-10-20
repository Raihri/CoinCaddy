import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, todayIso } from "../api";
import { useSession } from "../auth/session";
import { formatMoney, parseMajorToMinor } from "../money";
import { errorFor, type FieldErrors } from "../settings/apiErrors";
import { exponentOf, Field } from "../settings/fields";
import { CATEGORY_LABELS, OB_STATUS, PAYABLE_CATEGORIES, RECEIVABLE_CATEGORIES, type DuesSummary, type Obligation } from "./types";

export function ObligationsPage() {
  const { active, options } = useSession();
  const [tab, setTab] = useState<"receivable" | "payable">("receivable");
  const [items, setItems] = useState<Obligation[] | null>(null);
  const [summary, setSummary] = useState<DuesSummary | null>(null);
  const [showForm, setShowForm] = useState(false);
  const base = active ? `/api/businesses/${active.business_id}/obligations` : "";
  const canWrite = active?.allowed_actions.includes("obligations.write") ?? false;
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));

  const load = useCallback(async () => {
    if (!base) return;
    const [l, s] = await Promise.all([api<{ items: Obligation[] }>(base), api<DuesSummary>(`${base}/summary`)]);
    if (l.ok) setItems(l.data.items);
    if (s.ok) setSummary(s.data);
  }, [base]);
  useEffect(() => { load(); }, [load]);

  const shown = (items ?? []).filter((o) => o.direction === tab);
  return (
    <>
      <div className="page-header">
        <div>
          <p className="muted small"><Link className="link" to="/records">Records</Link> / Invoices &amp; bills</p>
          <h1 className="page-title">Invoices &amp; bills</h1>
        </div>
        {canWrite && <button type="button" className="btn btn-primary" onClick={() => setShowForm((v) => !v)}>{showForm ? "Close form" : "New invoice or bill"}</button>}
      </div>

      {showForm && <NewObligationForm base={base} onCreated={() => { setShowForm(false); load(); }} />}

      <div className="split split-even">
        <section className="card" aria-labelledby="dues-title">
          <h2 id="dues-title" className="card-title">Customer dues</h2>
          {summary?.customer_dues.length === 0 && <p className="muted">Nothing outstanding from customers.</p>}
          {summary?.customer_dues.map((d) => (
            <dl key={d.currency} className="lines">
              <div><dt>Due this week</dt><dd className="num">{money(d.due_this_week, d.currency)}</dd></div>
              <div><dt>Overdue 1–30 days</dt><dd className="num">{money(d.overdue_1_30, d.currency)}</dd></div>
              <div><dt>Overdue 30+ days</dt><dd className="num">{money(d.overdue_31_plus, d.currency)}</dd></div>
              <div><dt>Due later</dt><dd className="num">{money(d.due_later, d.currency)}</dd></div>
              <div><dt>Disputed (shown separately)</dt><dd className="num">{money(d.disputed, d.currency)}</dd></div>
              <div className="lines-total"><dt>Total outstanding · {d.currency}</dt><dd className="num">{money(d.total_outstanding, d.currency)}</dd></div>
            </dl>
          ))}
        </section>
        <section className="card" aria-labelledby="upcoming-title">
          <h2 id="upcoming-title" className="card-title">Upcoming supplier payments</h2>
          {summary?.upcoming_payables.length === 0 && <p className="muted">No bills due soon.</p>}
          {summary?.upcoming_payables.map((u) => (
            <div key={u.currency}>
              <ul className="movement-list">
                {u.items.map((i) => (
                  <li key={i.id}>
                    <Link to={`/records/obligations/${i.id}`} className="movement">
                      <span><span className="muted small block">{fmtDate(i.due_date)}{i.overdue ? ` · ${i.days_overdue} days overdue` : ""}</span>
                        <strong>{i.party.name}</strong> <span className="muted">{i.document_number}</span></span>
                      <span className="num neg">-{money(i.outstanding_minor, i.currency)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
              <p className="lines-total small"><span>Total · {u.currency}</span><strong className="num">{money(u.total, u.currency)}</strong></p>
            </div>
          ))}
        </section>
      </div>

      <section className="card" aria-labelledby="list-title">
        <div className="card-head">
          <h2 id="list-title" className="card-title">All {tab === "receivable" ? "customer invoices" : "supplier bills"}</h2>
        </div>
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "receivable"} className={`tab ${tab === "receivable" ? "is-active" : ""}`} onClick={() => setTab("receivable")}>Customer invoices</button>
          <button type="button" role="tab" aria-selected={tab === "payable"} className={`tab ${tab === "payable" ? "is-active" : ""}`} onClick={() => setTab("payable")}>Supplier bills</button>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Number</th><th>{tab === "receivable" ? "Customer" : "Supplier"}</th><th>Due</th><th className="right">Amount</th><th className="right">Outstanding</th><th>Status</th></tr></thead>
            <tbody>
              {items && shown.length === 0 && <tr><td colSpan={6} className="muted">None yet.</td></tr>}
              {shown.map((o) => (
                <tr key={o.id}>
                  <td><Link className="link" to={`/records/obligations/${o.id}`}>{o.document_number}</Link></td>
                  <td>{o.party.name}</td>
                  <td className="nowrap">{fmtDate(o.due_date)}</td>
                  <td className="num right">{money(o.amount_minor, o.currency)}</td>
                  <td className="num right">{money(o.outstanding_minor, o.currency)}</td>
                  <td className="badges">
                    <span className={`badge ${OB_STATUS[o.status][0]}`}>{OB_STATUS[o.status][1]}</span>
                    {o.overdue && <span className="badge badge-danger">{o.days_overdue}d overdue</span>}
                    {o.disputed && <span className="badge badge-warning">Disputed</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function NewObligationForm({ base, onCreated }: { base: string; onCreated: () => void }) {
  const { profile, options } = useSession();
  const navigate = useNavigate();
  const currency = profile?.base_currency ?? "BDT";
  const [f, setF] = useState({ direction: "receivable" as "receivable" | "payable", party_name: "", document_number: "", issue_date: todayIso(),
                               due_date: "", amount: "", category: "sales", source_reference: "", description: "", settlement_account_id: "" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const cats = f.direction === "receivable" ? RECEIVABLE_CATEGORIES : PAYABLE_CATEGORIES;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(f.amount, exponentOf(options, currency), 1e15);
    if (!parsed.ok) return setErrors({ amount_minor: parsed.error });
    if (!f.due_date) return setErrors({ due_date: "Choose a due date." });
    const body: Record<string, unknown> = { direction: f.direction, party_name: f.party_name, document_number: f.document_number,
      issue_date: f.issue_date, due_date: f.due_date, amount_minor: parsed.minor, currency, category: f.category, source_reference: f.source_reference };
    if (f.description.trim()) body.description = f.description;
    if (f.settlement_account_id) body.settlement_account_id = f.settlement_account_id;
    const r = await api<Obligation>(base, "POST", body);
    if (!r.ok) return setErrors(r.errors);
    onCreated();
    navigate(`/records/obligations/${r.data.id}`);
  };
  return (
    <form className="card form-grid" onSubmit={submit} noValidate aria-label="New invoice or bill">
      <h2 className="card-title span-2">New invoice or bill</h2>
      {(errors._form || errors[""]) && <p className="badge badge-danger span-2" role="alert">{errors._form ?? errors[""]}</p>}
      <Field label="Type">
        <select className="select" value={f.direction} onChange={(e) => {
          const d = e.target.value as "receivable" | "payable";
          setF({ ...f, direction: d, category: d === "receivable" ? "sales" : "inventory_suppliers" });
        }}>
          <option value="receivable">Customer invoice (money in)</option>
          <option value="payable">Supplier bill (money out)</option>
        </select>
      </Field>
      <Field label={f.direction === "receivable" ? "Customer" : "Supplier"} error={errorFor(errors, "party_name")}>
        <input value={f.party_name} maxLength={120} onChange={(e) => setF({ ...f, party_name: e.target.value })} />
      </Field>
      <Field label="Invoice / bill number" error={errorFor(errors, "document_number")}>
        <input value={f.document_number} maxLength={60} onChange={(e) => setF({ ...f, document_number: e.target.value })} />
      </Field>
      <Field label={`Amount (${currency})`} error={errorFor(errors, "amount_minor", "currency")}>
        <input inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
      </Field>
      <Field label="Issue date" error={errorFor(errors, "issue_date")}>
        <input type="date" value={f.issue_date} max={todayIso()} onChange={(e) => setF({ ...f, issue_date: e.target.value })} />
      </Field>
      <Field label="Due date" error={errorFor(errors, "due_date")}>
        <input type="date" value={f.due_date} min={f.issue_date} onChange={(e) => setF({ ...f, due_date: e.target.value })} />
      </Field>
      <Field label="Category" error={errorFor(errors, "category")}>
        <select className="select" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
          {cats.map((c) => <option key={c} value={c}>{CATEGORY_LABELS[c]}</option>)}
        </select>
      </Field>
      <Field label="Source reference" error={errorFor(errors, "source_reference")}>
        <input value={f.source_reference} maxLength={120} onChange={(e) => setF({ ...f, source_reference: e.target.value })} />
      </Field>
      <Field label="Expected account (optional)" error={errorFor(errors, "settlement_account_id")}>
        <select className="select" value={f.settlement_account_id} onChange={(e) => setF({ ...f, settlement_account_id: e.target.value })}>
          <option value="">Not specified</option>
          {profile?.accounts.filter((a) => a.currency === currency).map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      </Field>
      <Field label="Description (optional)" error={errorFor(errors, "description")}>
        <input value={f.description} maxLength={200} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field>
      <div className="form-actions span-2"><button type="submit" className="btn btn-primary">Save</button></div>
    </form>
  );
}

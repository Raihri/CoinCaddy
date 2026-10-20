import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import { exponentOf, Field } from "../settings/fields";
import { VoidButton } from "./AccountPage";
import type { CashTxn, Position } from "./types";

type LedgerTxn = CashTxn & { account_label: string };

export function TransfersPage() {
  const { active, options } = useSession();
  const [pos, setPos] = useState<Position | null>(null);
  const [ledger, setLedger] = useState<LedgerTxn[]>([]);
  const [message, setMessage] = useState<{ kind: string; text: string } | null>(null);
  const [declare, setDeclare] = useState("");
  const base = active ? `/api/businesses/${active.business_id}` : "";
  const canManage = active?.allowed_actions.includes("cash.manage") ?? false;

  const load = useCallback(async () => {
    if (!base) return;
    const [p, l] = await Promise.all([api<Position>(`${base}/cash/position`), api<{ transactions: LedgerTxn[] }>(`${base}/ledger?limit=200`)]);
    if (p.ok) setPos(p.data);
    if (l.ok) setLedger(l.data.transactions);
  }, [base]);
  useEffect(() => { load(); }, [load]);

  if (!pos) return <p className="muted">Loading…</p>;
  const byId = new Map(ledger.map((t) => [t.id, t]));
  const linked = new Set(pos.transfers.flatMap((t) => [t.out?.id, t.in?.id]).filter(Boolean));
  const money = (m: number, c: string) => formatMoney(m, c, exponentOf(options, c));
  const send = async (body: object, ok: string) => {
    const r = await api<Position>(`${base}/cash/transfers`, "POST", body);
    if (r.ok) { setPos(r.data); setMessage({ kind: "success", text: ok }); } else setMessage({ kind: "danger", text: r.message });
  };
  const side = (t: CashTxn | null | undefined) => t
    ? <span>{fmtDate(t.business_date)} · {byId.get(t.id)?.account_label ?? "Account"} · {t.description}</span>
    : <span className="field-error">Missing</span>;

  return (
    <>
      <div className="page-header">
        <div>
          <p className="muted small"><Link className="link" to="/records">Records</Link> / Transfers</p>
          <h1 className="page-title">Own-account transfers</h1>
        </div>
      </div>
      {message && <p className={`badge badge-${message.kind}`} role="status">{message.text}</p>}

      <section className="card" aria-labelledby="sugg-title">
        <h2 id="sugg-title" className="card-title">Suggested pairs</h2>
        {pos.transfer_suggestions.length === 0 && <p className="muted">No suggestions.</p>}
        <ul className="pair-list">
          {pos.transfer_suggestions.map((s) => (
            <li key={s.out_txn_id} className="pair">
              <span className="pair-sides">
                <span><span className="muted small block">Out</span>{side(byId.get(s.out_txn_id))}</span>
                <span><span className="muted small block">In</span>{side(byId.get(s.in_txn_id))}</span>
              </span>
              <strong className="num">{money(s.amount_minor, s.currency)}</strong>
              {canManage && <button type="button" className="btn btn-primary btn-small"
                                    onClick={() => send({ out_txn_id: s.out_txn_id, in_txn_id: s.in_txn_id }, "Transfer linked.")}>Link as transfer</button>}
            </li>
          ))}
        </ul>
      </section>

      <section className="card" aria-labelledby="links-title">
        <h2 id="links-title" className="card-title">Declared transfers</h2>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Out</th><th>In</th><th className="right">Amount</th><th>Status</th>{canManage && <th />}</tr></thead>
            <tbody>
              {pos.transfers.length === 0 && <tr><td colSpan={5} className="muted">None yet.</td></tr>}
              {pos.transfers.map((t) => (
                <tr key={t.id}>
                  <td className="wrap">{side(t.out)}</td>
                  <td className="wrap">{side(t.in)}</td>
                  <td className="num right">{t.amount_minor !== null && t.currency ? money(t.amount_minor, t.currency) : "—"}</td>
                  <td>{t.status === "matched" ? <span className="badge badge-success">Matched</span>
                        : <span className="badge badge-danger">Unmatched · {t.missing_side} side missing</span>}</td>
                  {canManage && <td><VoidButton onVoid={async (reason) => {
                    const r = await api<Position>(`${base}/cash/transfers/${t.id}/void`, "POST", { reason });
                    if (r.ok) { setPos(r.data); return null; } return r.message;
                  }} /></td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {canManage && (
          <div className="declare-row">
            <Field label="Declare a one-sided transfer (the other account is not tracked yet)">
              <select className="select" value={declare} onChange={(e) => setDeclare(e.target.value)}>
                <option value="">Choose a transaction</option>
                {ledger.filter((t) => !linked.has(t.id)).map((t) => (
                  <option key={t.id} value={t.id}>{t.business_date} · {t.account_label} · {t.description} · {money(t.amount_minor, t.currency)}</option>
                ))}
              </select>
            </Field>
            <button type="button" className="btn btn-ghost" disabled={!declare} onClick={() => {
              const t = byId.get(declare)!;
              send(t.amount_minor < 0 ? { out_txn_id: t.id } : { in_txn_id: t.id }, "Declared. It stays flagged until the other side is linked.");
              setDeclare("");
            }}>Declare</button>
          </div>
        )}
      </section>
    </>
  );
}

"""Financial Health: a cash-basis operating summary from accepted (posted) records. Calculation fh-v1.

This is NOT an audited profit and loss statement. It counts settled cash movements only:
- operating receipts and payments by category, from classified posted transactions;
- own-account transfers (Prompt 05) are excluded from every operating figure;
- financing inflows/outflows, owner contributions/drawings, and other non-operating movements are
  reported separately and never counted as revenue or expenses;
- unclassified posted transactions are reported as such and make the period "incomplete".
All arithmetic is integer minor units, per currency. Every figure carries its source transaction IDs.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.cash.service import find_gaps, merge_intervals

CALC_VERSION = "fh-v1"
RECEIPT_CATEGORIES = ("sales", "other_income")
PAYMENT_CATEGORIES = ("inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing",
                      "fees_charges", "tax", "other_expense")
INFLOW_KINDS = ("operating_receipt", "financing_inflow", "owner_contribution")
OUTFLOW_KINDS = ("operating_payment", "financing_outflow", "owner_drawing")
KINDS = INFLOW_KINDS + OUTFLOW_KINDS + ("other_non_operating",)
TREND_MONTHS = 6


@dataclass(frozen=True)
class Row:
    id: str
    account_id: str
    business_date: date
    amount_minor: int
    currency: str
    description: str
    kind: str | None        # None = unclassified; "own_transfer" when part of a declared transfer
    category: str | None


def month_bounds(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return start, end


def previous_months(month: str, n: int) -> list[str]:
    y, m = (int(x) for x in month.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def margin_basis_points(net: int, receipts: int) -> int | None:
    """net / receipts in basis points, rounded half away from zero, exact integer arithmetic."""
    if receipts <= 0:
        return None
    q, r = divmod(abs(net) * 10_000, receipts)
    q += 1 if 2 * r >= receipts else 0
    return q if net >= 0 else -q


def summarize_month(rows: list[Row], month: str, currency: str) -> dict:
    start, end = month_bounds(month)
    rows = [r for r in rows if r.currency == currency and start <= r.business_date <= end]
    rec = defaultdict(lambda: {"amount_minor": 0, "transaction_ids": []})
    pay = defaultdict(lambda: {"amount_minor": 0, "transaction_ids": []})
    other = defaultdict(lambda: {"in_minor": 0, "out_minor": 0, "transaction_ids": []})
    unclassified = {"count": 0, "in_minor": 0, "out_minor": 0, "transaction_ids": []}
    for r in rows:
        if r.kind == "operating_receipt":
            rec[r.category]["amount_minor"] += r.amount_minor
            rec[r.category]["transaction_ids"].append(r.id)
        elif r.kind == "operating_payment":
            pay[r.category]["amount_minor"] += -r.amount_minor
            pay[r.category]["transaction_ids"].append(r.id)
        elif r.kind is None:
            unclassified["count"] += 1
            unclassified["in_minor" if r.amount_minor > 0 else "out_minor"] += abs(r.amount_minor)
            unclassified["transaction_ids"].append(r.id)
        else:  # own_transfer, financing, owner, other non-operating: excluded from operating figures
            o = other[r.kind]
            o["in_minor" if r.amount_minor > 0 else "out_minor"] += abs(r.amount_minor)
            o["transaction_ids"].append(r.id)
    receipts = sum(v["amount_minor"] for v in rec.values())
    payments = sum(v["amount_minor"] for v in pay.values())
    net = receipts - payments
    bp = margin_basis_points(net, receipts)
    return {
        "month": month, "currency": currency,
        "operating_receipts_minor": receipts, "operating_payments_minor": payments, "net_operating_cash_minor": net,
        "receipt_categories": [{"category": c, **v} for c, v in sorted(rec.items(), key=lambda kv: -kv[1]["amount_minor"])],
        "payment_categories": [{"category": c, **v} for c, v in sorted(pay.items(), key=lambda kv: -kv[1]["amount_minor"])],
        "margin": {"basis_points": bp, "available": bp is not None,
                   "reason": None if bp is not None else "No operating receipts in this month, so a margin cannot be calculated."},
        "excluded": {k: v for k, v in sorted(other.items())},
        "unclassified": unclassified,
        "complete": unclassified["count"] == 0,
        "transaction_count": len(rows),
    }


def coverage_status(accounts: list[dict], coverage: list[dict], rows: list[Row], month: str, as_of: date, currency: str) -> list[dict]:
    """For each account in this currency with posted movements in the month: days of the month (up to
    as_of) without declared source coverage. Missing coverage means figures may be incomplete."""
    start, end = month_bounds(month)
    end = min(end, as_of)
    out = []
    for a in accounts:
        if a["currency"] != currency or end < start:
            continue
        has_rows = any(r.account_id == a["id"] and start <= r.business_date <= end for r in rows)
        merged = merge_intervals([(c["period_start"], c["period_end"]) for c in coverage if c["account_id"] == a["id"]])
        gaps = find_gaps(merged, start, end)
        if has_rows or gaps != [(start, end)]:
            out.append({"account_id": a["id"], "label": a["label"], "has_movements": has_rows,
                        "uncovered_days": sum((e - s).days + 1 for s, e in gaps),
                        "gaps": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in gaps]})
    return out


def what_changed(cur: dict, prev: dict) -> str:
    """Deterministic plain-language comparison built from the computed numbers (no AI)."""
    def fmt(v: int) -> str:
        return f"{abs(v) // 100:,}.{abs(v) % 100:02d}"
    d_rec = cur["operating_receipts_minor"] - prev["operating_receipts_minor"]
    d_pay = cur["operating_payments_minor"] - prev["operating_payments_minor"]
    c = cur["currency"]
    parts = [f"Operating receipts {'rose' if d_rec > 0 else 'fell' if d_rec < 0 else 'were unchanged'}"
             + (f" by {c} {fmt(d_rec)}" if d_rec else "") + f" compared with {prev['month']}",
             f"operating payments {'rose' if d_pay > 0 else 'fell' if d_pay < 0 else 'were unchanged'}" + (f" by {c} {fmt(d_pay)}" if d_pay else "")]
    sentence = ", while ".join(parts) + "."
    if cur["receipt_categories"]:
        top = cur["receipt_categories"][0]
        sentence += f" The largest receipt category was {top['category'].replace('_', ' ')} ({c} {fmt(top['amount_minor'])})."
    if not cur["complete"]:
        sentence += f" {cur['unclassified']['count']} transaction(s) are not yet classified, so these figures are incomplete."
    return sentence


# --- loading ------------------------------------------------------------------------------------------


def load_rows(db: Session, business_id: UUID) -> list[Row]:
    transfers = {str(x) for r in db.execute(text(
        "SELECT out_txn_id, in_txn_id FROM own_transfer WHERE business_id = :b AND voided_at IS NULL"), {"b": business_id}).all() for x in r if x}
    rows = db.execute(text(
        """SELECT lt.id, lt.account_id, lt.business_date, lt.amount_minor, lt.currency, lt.description, c.kind, c.category
           FROM ledger_transaction lt LEFT JOIN txn_classification c ON c.txn_id = lt.id
           WHERE lt.business_id = :b"""), {"b": business_id}).mappings().all()
    return [Row(str(r["id"]), str(r["account_id"]), r["business_date"], r["amount_minor"], r["currency"], r["description"],
                "own_transfer" if str(r["id"]) in transfers else r["kind"], None if str(r["id"]) in transfers else r["category"])
            for r in rows]


def suggestions(db: Session, business_id: UUID) -> dict[str, dict]:
    """Deterministic suggestions from payments allocated in Receivables/Payables: a bank receipt that paid a
    sales invoice is suggested as an operating receipt of that category, and so on."""
    out = {}
    for r in db.execute(text(
        """SELECT DISTINCT ON (a.ledger_txn_id) a.ledger_txn_id, o.direction, o.category, o.document_number
           FROM allocation a JOIN obligation o ON o.id = a.obligation_id
           WHERE a.business_id = :b AND a.ledger_txn_id IS NOT NULL AND a.kind = 'payment'
             AND NOT EXISTS (SELECT 1 FROM allocation r WHERE r.reverses_id = a.id)
           ORDER BY a.ledger_txn_id, a.created_at"""), {"b": business_id}).mappings():
        if r["direction"] == "receivable":
            kind, cat = "operating_receipt", r["category"] if r["category"] in RECEIPT_CATEGORIES else "other_income"
        elif r["category"] == "loan_repayment":
            kind, cat = "financing_outflow", None
        else:
            kind, cat = "operating_payment", r["category"] if r["category"] in PAYMENT_CATEGORIES else "other_expense"
        out[str(r["ledger_txn_id"])] = {"kind": kind, "category": cat, "because": f"Paid {r['document_number']}"}
    return out

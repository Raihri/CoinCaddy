"""Read model for receivables and payables: outstanding amounts, status, ageing, and summaries.

Outstanding = original amount - sum(allocations), where reversals are negative allocations. The
original obligation row never changes, so history is always reconstructable.
"""

from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

RECEIVABLE_CATEGORIES = ("sales", "other_income")
PAYABLE_CATEGORIES = ("inventory_suppliers", "rent", "utilities", "salaries", "transport", "marketing",
                      "loan_repayment", "tax", "other_expense")
CATEGORIES = {"receivable": RECEIVABLE_CATEGORIES, "payable": PAYABLE_CATEGORIES}
UPCOMING_DAYS = 30

_BASE_SQL = """
    SELECT o.*, p.name AS party_name, ca.label AS settlement_account_label,
           coalesce(sum(a.amount_minor), 0)::bigint AS allocated_minor,
           count(a.id) FILTER (WHERE a.kind = 'payment') AS payment_count
    FROM obligation o
    JOIN party p ON p.business_id = o.business_id AND p.id = o.party_id
    LEFT JOIN cash_account ca ON ca.business_id = o.business_id AND ca.id = o.settlement_account_id
    LEFT JOIN allocation a ON a.obligation_id = o.id
    WHERE o.business_id = :b {where}
    GROUP BY o.id, p.name, ca.label
"""


def describe(r, today: date) -> dict:
    outstanding = r["amount_minor"] - r["allocated_minor"]
    if r["cancelled_at"]:
        status = "cancelled"
    elif outstanding == 0:
        status = "settled"
    elif outstanding < r["amount_minor"]:
        status = "partially_settled"
    else:
        status = "open"
    open_amount = status in ("open", "partially_settled")
    days_overdue = (today - r["due_date"]).days if open_amount and r["due_date"] < today else 0
    return {
        "id": str(r["id"]), "direction": r["direction"], "party": {"id": str(r["party_id"]), "name": r["party_name"]},
        "document_number": r["document_number"], "issue_date": r["issue_date"].isoformat(), "due_date": r["due_date"].isoformat(),
        "amount_minor": r["amount_minor"], "allocated_minor": r["allocated_minor"], "outstanding_minor": outstanding,
        "currency": r["currency"], "category": r["category"], "description": r["description"],
        "source_reference": r["source_reference"],
        "settlement_account": {"id": str(r["settlement_account_id"]), "label": r["settlement_account_label"]} if r["settlement_account_id"] else None,
        "disputed": r["disputed"], "dispute_reason": r["dispute_reason"],
        "cancelled": r["cancelled_at"] is not None, "cancel_reason": r["cancel_reason"],
        "status": status, "overdue": days_overdue > 0, "days_overdue": days_overdue,
        "created_at": r["created_at"].isoformat(),
    }


def list_obligations(db: Session, business_id: UUID, today: date, *, direction: str | None = None,
                     obligation_id: UUID | None = None) -> list[dict]:
    where, params = "", {"b": business_id}
    if direction:
        where += " AND o.direction = :d"
        params["d"] = direction
    if obligation_id:
        where += " AND o.id = :o"
        params["o"] = obligation_id
    rows = db.execute(text(_BASE_SQL.format(where=where) + " ORDER BY o.due_date, o.document_number"), params).mappings().all()
    return [describe(r, today) for r in rows]


def allocations(db: Session, obligation_id: UUID) -> list[dict]:
    rows = db.execute(text(
        """SELECT a.*, u.display_name AS created_by_name, lt.business_date AS txn_date, lt.description AS txn_description,
                  lt.amount_minor AS txn_amount, r.id AS reversed_by
           FROM allocation a JOIN app_user u ON u.id = a.created_by
           LEFT JOIN ledger_transaction lt ON lt.id = a.ledger_txn_id
           LEFT JOIN allocation r ON r.reverses_id = a.id
           WHERE a.obligation_id = :o ORDER BY a.created_at, a.id"""), {"o": obligation_id}).mappings().all()
    return [{
        "id": str(r["id"]), "kind": r["kind"], "amount_minor": r["amount_minor"], "currency": r["currency"],
        "allocation_date": r["allocation_date"].isoformat(), "note": r["note"], "created_by": r["created_by_name"],
        "created_at": r["created_at"].isoformat(), "reverses_id": str(r["reverses_id"]) if r["reverses_id"] else None,
        "reversed": r["reversed_by"] is not None,
        "ledger_txn": None if r["ledger_txn_id"] is None else {
            "id": str(r["ledger_txn_id"]), "business_date": r["txn_date"].isoformat(), "description": r["txn_description"],
            "amount_minor": r["txn_amount"]},
    } for r in rows]


def summary(items: list[dict], today: date) -> dict:
    """Customer dues by age bucket and upcoming supplier payments, per currency. Disputed amounts are shown
    separately and never mixed into the buckets."""
    dues: dict[str, dict] = defaultdict(lambda: {"due_later": 0, "due_this_week": 0, "overdue_1_30": 0, "overdue_31_plus": 0,
                                                 "disputed": 0, "total_outstanding": 0, "overdue_count": 0, "customers_overdue": set()})
    upcoming: dict[str, dict] = defaultdict(lambda: {"total": 0, "overdue_total": 0, "items": []})
    week_end, horizon = today + timedelta(days=7), today + timedelta(days=UPCOMING_DAYS)
    for o in items:
        if o["status"] not in ("open", "partially_settled"):
            continue
        amt, cur, due = o["outstanding_minor"], o["currency"], date.fromisoformat(o["due_date"])
        if o["direction"] == "receivable":
            d = dues[cur]
            d["total_outstanding"] += amt
            if o["disputed"]:
                d["disputed"] += amt
            elif o["days_overdue"] > 30:
                d["overdue_31_plus"] += amt
            elif o["days_overdue"] > 0:
                d["overdue_1_30"] += amt
            elif due < week_end:
                d["due_this_week"] += amt
            else:
                d["due_later"] += amt
            if o["days_overdue"] > 0 and not o["disputed"]:  # counts match the overdue buckets, which exclude disputes
                d["overdue_count"] += 1
                d["customers_overdue"].add(o["party"]["id"])
        elif due <= horizon:
            u = upcoming[cur]
            u["total"] += amt
            if o["overdue"]:
                u["overdue_total"] += amt
            u["items"].append({k: o[k] for k in ("id", "party", "document_number", "due_date", "outstanding_minor",
                                                  "currency", "category", "overdue", "days_overdue", "disputed")})
    return {
        "as_of": today.isoformat(), "upcoming_days": UPCOMING_DAYS,
        "customer_dues": [{"currency": c, **{k: v for k, v in d.items() if k != "customers_overdue"},
                           "customers_overdue": len(d["customers_overdue"])} for c, d in sorted(dues.items())],
        "upcoming_payables": [{"currency": c, **u} for c, u in sorted(upcoming.items())],
    }

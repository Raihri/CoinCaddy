"""Financial Health API: cash-basis operating summary, source drill-down, and transaction classification."""

import re
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.money import MINOR_UNIT_EXPONENT
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.health_summary.service import (
    CALC_VERSION, INFLOW_KINDS, KINDS, OUTFLOW_KINDS, PAYMENT_CATEGORIES, RECEIPT_CATEGORIES, TREND_MONTHS,
    coverage_status, load_rows, month_bounds, previous_months, suggestions, summarize_month, what_changed,
)
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations, summary as dues_summary
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}", tags=["financial health"])
NOTICE = ("Cash-basis operating summary of settled, posted records. It is not an audited profit and loss "
          "statement: unpaid invoices and bills are shown separately, and own-account transfers, financing, "
          "and owner money are never counted as revenue or expenses.")


class ClassifyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn_id: UUID
    kind: Literal["operating_receipt", "operating_payment", "financing_inflow", "financing_outflow",
                  "owner_contribution", "owner_drawing", "other_non_operating"]
    category: StrictStr | None = None

    @model_validator(mode="after")
    def _category(self) -> "ClassifyItem":
        allowed = {"operating_receipt": RECEIPT_CATEGORIES, "operating_payment": PAYMENT_CATEGORIES}.get(self.kind)
        if allowed is None and self.category is not None:
            raise ValueError(f"{self.kind} takes no category")
        if allowed is not None and self.category not in allowed:
            raise ValueError(f"category for {self.kind} must be one of {', '.join(allowed)}")
        return self


class ClassifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClassifyItem] = Field(min_length=1, max_length=200)


def _ctx_dates(db: Session, business_id: UUID) -> tuple[date, str]:
    tz, base = db.execute(text("SELECT timezone, base_currency FROM business WHERE id = :b"), {"b": business_id}).one()
    return business_today(tz), base


def _check_month(month: str | None, today: date) -> str:
    current = f"{today.year:04d}-{today.month:02d}"
    if month is None:
        return current
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or not "2000-01" <= month <= current:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["query", "month"], "msg": f"month must be YYYY-MM between 2000-01 and {current}", "type": "value_error"}])
    return month


def _check_currency(currency: str | None, base: str) -> str:
    c = (currency or base).upper()
    if c not in MINOR_UNIT_EXPONENT:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["query", "currency"], "msg": "unsupported currency", "type": "value_error"}])
    return c


@router.get("/financial-health")
def financial_health(month: str | None = Query(None), currency: str | None = Query(None),
                     ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    today, base = _ctx_dates(db, ctx.business_id)
    month, currency = _check_month(month, today), _check_currency(currency, base)
    rows = load_rows(db, ctx.business_id)
    months = previous_months(month, TREND_MONTHS)
    trend = [summarize_month(rows, m, currency) for m in months]
    cur, prev = trend[-1], trend[-2]
    accounts = [{"id": str(r.id), "label": r.label, "currency": r.currency} for r in db.execute(
        text("SELECT id, label, currency FROM cash_account WHERE business_id = :b ORDER BY created_at"), {"b": ctx.business_id})]
    coverage = [{"account_id": str(r.account_id), "period_start": r.period_start, "period_end": r.period_end} for r in db.execute(
        text("SELECT account_id, period_start, period_end FROM coverage_period WHERE business_id = :b AND voided_at IS NULL"), {"b": ctx.business_id})]
    dues = dues_summary(list_obligations(db, ctx.business_id, today), today)
    return {
        "calc_version": CALC_VERSION, "as_of": today.isoformat(), "notice": NOTICE,
        "currencies": sorted({r.currency for r in rows} | {base}),
        "summary": cur,
        "previous": {k: prev[k] for k in ("month", "operating_receipts_minor", "operating_payments_minor", "net_operating_cash_minor", "complete")},
        "trend": [{k: t[k] for k in ("month", "operating_receipts_minor", "operating_payments_minor", "net_operating_cash_minor", "complete")} for t in trend],
        "what_changed": what_changed(cur, prev),
        "coverage": coverage_status(accounts, coverage, rows, month, today, currency),
        "customer_dues": next((d for d in dues["customer_dues"] if d["currency"] == currency), None),
        "upcoming_payables": next((u for u in dues["upcoming_payables"] if u["currency"] == currency), None),
    }


@router.get("/financial-health/transactions")
def source_transactions(month: str = Query(...), currency: str | None = Query(None),
                        kind: Literal["operating_receipt", "operating_payment", "financing_inflow", "financing_outflow",
                                      "owner_contribution", "owner_drawing", "other_non_operating", "own_transfer", "unclassified"] = Query(...),
                        category: str | None = Query(None),
                        ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    """The exact posted transactions behind one figure of the summary."""
    today, base = _ctx_dates(db, ctx.business_id)
    month, currency = _check_month(month, today), _check_currency(currency, base)
    s = summarize_month(load_rows(db, ctx.business_id), month, currency)
    if kind == "unclassified":
        ids = s["unclassified"]["transaction_ids"]
    elif kind in ("operating_receipt", "operating_payment"):
        cats = s["receipt_categories" if kind == "operating_receipt" else "payment_categories"]
        ids = [i for c in cats if category in (None, c["category"]) for i in c["transaction_ids"]]
    else:
        ids = s["excluded"].get(kind, {}).get("transaction_ids", [])
    rows = db.execute(text(
        """SELECT lt.id, lt.business_date, lt.amount_minor, lt.currency, lt.description, lt.reference, ca.label AS account_label,
                  lt.source_batch_id, lt.source_row_number
           FROM ledger_transaction lt JOIN cash_account ca ON ca.business_id = lt.business_id AND ca.id = lt.account_id
           WHERE lt.business_id = :b AND lt.id = ANY(:ids) ORDER BY lt.business_date, lt.source_row_number"""),
        {"b": ctx.business_id, "ids": [UUID(i) for i in ids]}).mappings().all()
    return {"month": month, "currency": currency, "kind": kind, "category": category,
            "total_minor": sum(abs(r["amount_minor"]) for r in rows),
            "transactions": [{**{k: r[k] for k in ("amount_minor", "currency", "description", "reference", "account_label", "source_row_number")},
                              "id": str(r["id"]), "business_date": r["business_date"].isoformat(), "source_batch_id": str(r["source_batch_id"])}
                             for r in rows]}


@router.get("/classifications")
def classifications(month: str | None = Query(None), only_unclassified: bool = Query(False),
                    ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    today, _ = _ctx_dates(db, ctx.business_id)
    rows = load_rows(db, ctx.business_id)
    if month is not None:
        start, end = month_bounds(_check_month(month, today))
        rows = [r for r in rows if start <= r.business_date <= end]
    sugg = suggestions(db, ctx.business_id)
    labels = {str(r.id): r.label for r in db.execute(text("SELECT id, label FROM cash_account WHERE business_id = :b"), {"b": ctx.business_id})}
    items = [{"id": r.id, "business_date": r.business_date.isoformat(), "amount_minor": r.amount_minor, "currency": r.currency,
              "description": r.description, "account_label": labels.get(r.account_id), "kind": r.kind, "category": r.category,
              "suggestion": sugg.get(r.id) if r.kind is None else None}
             for r in sorted(rows, key=lambda r: (r.business_date, r.id)) if not only_unclassified or r.kind is None]
    return {"items": items, "unclassified_count": sum(1 for r in rows if r.kind is None)}


@router.post("/classifications")
def classify(body: ClassifyIn, ctx: BusinessContext = Depends(require("ledger.classify")), db: Session = Depends(get_session)) -> dict:
    ids = [i.txn_id for i in body.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "items"], "msg": "each transaction may appear once", "type": "value_error"}])
    txns = {r.id: r for r in db.execute(text(
        "SELECT id, amount_minor FROM ledger_transaction WHERE business_id = :b AND id = ANY(:ids) FOR UPDATE"), {"b": ctx.business_id, "ids": ids})}
    if len(txns) != len(ids):  # includes transactions of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    transfers = {x for r in db.execute(text(
        "SELECT out_txn_id, in_txn_id FROM own_transfer WHERE business_id = :b AND voided_at IS NULL"), {"b": ctx.business_id}).all() for x in r if x}
    previous = {r.txn_id: r.kind for r in db.execute(text("SELECT txn_id, kind FROM txn_classification WHERE txn_id = ANY(:ids)"), {"ids": ids})}
    for n, item in enumerate(body.items):
        amount = txns[item.txn_id].amount_minor
        if item.txn_id in transfers:
            raise HTTPException(status.HTTP_409_CONFLICT, "An own-account transfer is never income or spending; void the transfer first to classify it.")
        if (item.kind in INFLOW_KINDS and amount <= 0) or (item.kind in OUTFLOW_KINDS and amount >= 0):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "items", n, "kind"], "type": "value_error",
                                                                         "msg": f"{item.kind} needs money {'in' if item.kind in INFLOW_KINDS else 'out'}"}])
    for item in body.items:
        db.execute(text(
            """INSERT INTO txn_classification (business_id, txn_id, kind, category, classified_by) VALUES (:b, :t, :k, :c, :u)
               ON CONFLICT (txn_id) DO UPDATE SET kind = EXCLUDED.kind, category = EXCLUDED.category,
                   classified_by = EXCLUDED.classified_by, classified_at = now()"""),
            {"b": ctx.business_id, "t": item.txn_id, "k": item.kind, "c": item.category, "u": ctx.user.id})
    record_event(db, action="ledger.classify", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role),
                 business_id=ctx.business_id, target_type="ledger_transaction",
                 details={"count": len(body.items), "changes": [{"txn_id": str(i.txn_id), "from": previous.get(i.txn_id), "to": i.kind,
                                                                 "category": i.category} for i in body.items[:50]]})
    db.commit()
    return {"classified": len(body.items)}

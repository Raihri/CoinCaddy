"""Receivables and Payables API: create invoices/bills, allocate payments, reverse, dispute, cancel."""

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import check_currency, clean_text
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import CATEGORIES, allocations, list_obligations, summary
from app.modules.records.normalize import looks_like_formula
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/obligations", tags=["receivables and payables"])
EARLIEST = date(2000, 1, 1)


def _text(max_len: int):
    def check(v: str | None) -> str | None:
        if v is None:
            return v
        v = clean_text(v, max_len)
        if looks_like_formula(v):
            raise ValueError("must not look like a spreadsheet formula")
        return v
    return check


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObligationIn(_Strict):
    direction: Literal["receivable", "payable"]
    party_name: StrictStr
    document_number: StrictStr
    issue_date: date
    due_date: date
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    currency: StrictStr
    category: StrictStr
    source_reference: StrictStr
    description: StrictStr | None = None
    settlement_account_id: UUID | None = None

    validate_party = field_validator("party_name")(_text(120))
    validate_number = field_validator("document_number")(_text(60))
    validate_ref = field_validator("source_reference")(_text(120))
    validate_desc = field_validator("description")(_text(200))
    validate_currency = field_validator("currency")(check_currency)

    @model_validator(mode="after")
    def _rules(self) -> "ObligationIn":
        if self.category not in CATEGORIES[self.direction]:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES[self.direction])} for a {self.direction}")
        if self.issue_date < EARLIEST:
            raise ValueError("issue_date must not be before 2000-01-01")
        if self.due_date < self.issue_date:
            raise ValueError("due_date must not be before issue_date")
        if (self.due_date - self.issue_date).days > 1830:
            raise ValueError("due_date must be within five years of issue_date")
        return self


class AllocationIn(_Strict):
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    currency: StrictStr
    allocation_date: date
    ledger_txn_id: UUID | None = None
    note: StrictStr | None = None

    validate_currency = field_validator("currency")(check_currency)
    validate_note = field_validator("note")(_text(200))


class ReversalIn(_Strict):
    reason: StrictStr
    reversal_date: date

    validate_reason = field_validator("reason")(_text(200))


class DisputeIn(_Strict):
    disputed: StrictBool
    reason: StrictStr | None = None

    validate_reason = field_validator("reason")(_text(200))

    @model_validator(mode="after")
    def _reason(self) -> "DisputeIn":
        if self.disputed and not self.reason:
            raise ValueError("a reason is required when marking a dispute")
        if not self.disputed and self.reason:
            raise ValueError("leave the reason empty when clearing a dispute")
        return self


class CancelIn(_Strict):
    reason: StrictStr

    validate_reason = field_validator("reason")(_text(200))


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _unprocessable(field_name: str, msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", field_name], "msg": msg, "type": "value_error"}])


def _today(db: Session, business_id: UUID) -> date:
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


def _as_of(db: Session, business_id: UUID, as_of: date | None) -> date:
    today = _today(db, business_id)
    if as_of is not None and not EARLIEST <= as_of <= today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["query", "as_of"], "msg": "as_of must be between 2000-01-01 and today", "type": "value_error"}])
    return as_of or today


def _detail(db: Session, business_id: UUID, obligation_id: UUID, today: date) -> dict:
    found = list_obligations(db, business_id, today, obligation_id=obligation_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice or bill not found")
    return found[0] | {"allocations": allocations(db, obligation_id)}


def _lock(db: Session, business_id: UUID, obligation_id: UUID):
    row = db.execute(text("SELECT * FROM obligation WHERE business_id = :b AND id = :o FOR UPDATE"),
                     {"b": business_id, "o": obligation_id}).mappings().first()
    if row is None:  # includes an obligation of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice or bill not found")
    allocated = db.execute(text("SELECT coalesce(sum(amount_minor), 0) FROM allocation WHERE obligation_id = :o"),
                           {"o": obligation_id}).scalar_one()
    return row, int(allocated)


# --- reads --------------------------------------------------------------------------------------------


@router.get("")
def list_all(direction: Literal["receivable", "payable"] | None = None, as_of: date | None = Query(None),
             ctx: BusinessContext = Depends(require("obligations.read")), db: Session = Depends(get_session)) -> dict:
    today = _as_of(db, ctx.business_id, as_of)
    return {"as_of": today.isoformat(), "items": list_obligations(db, ctx.business_id, today, direction=direction)}


@router.get("/summary")
def dues_summary(as_of: date | None = Query(None), ctx: BusinessContext = Depends(require("obligations.read")),
                 db: Session = Depends(get_session)) -> dict:
    today = _as_of(db, ctx.business_id, as_of)
    return summary(list_obligations(db, ctx.business_id, today), today)


@router.get("/{obligation_id}")
def get_one(obligation_id: UUID, as_of: date | None = Query(None), ctx: BusinessContext = Depends(require("obligations.read")),
            db: Session = Depends(get_session)) -> dict:
    return _detail(db, ctx.business_id, obligation_id, _as_of(db, ctx.business_id, as_of))


# --- writes -------------------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: ObligationIn, ctx: BusinessContext = Depends(require("obligations.write")), db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    if body.issue_date > today:
        raise _unprocessable("issue_date", "must not be in the future (business timezone)")
    if body.settlement_account_id is not None:
        acc = db.execute(text("SELECT currency FROM cash_account WHERE business_id = :b AND id = :a"),
                         {"b": ctx.business_id, "a": body.settlement_account_id}).scalar_one_or_none()
        if acc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
        if acc != body.currency:
            raise _unprocessable("settlement_account_id", f"the account is in {acc}, the {body.direction} in {body.currency}")
    party_id = db.execute(text("SELECT id FROM party WHERE business_id = :b AND lower(name) = lower(:n)"),
                          {"b": ctx.business_id, "n": body.party_name}).scalar_one_or_none()
    try:
        with db.begin_nested():
            if party_id is None:
                party_id = db.execute(text("INSERT INTO party (business_id, name, created_by) VALUES (:b, :n, :u) RETURNING id"),
                                      {"b": ctx.business_id, "n": body.party_name, "u": ctx.user.id}).scalar_one()
            oid = db.execute(text(
                """INSERT INTO obligation (business_id, direction, party_id, document_number, issue_date, due_date, amount_minor,
                                           currency, category, description, source_reference, settlement_account_id, created_by)
                   VALUES (:b, :dir, :p, :num, :iss, :due, :amt, :cur, :cat, :desc, :ref, :acc, :u) RETURNING id"""),
                {"b": ctx.business_id, "dir": body.direction, "p": party_id, "num": body.document_number, "iss": body.issue_date,
                 "due": body.due_date, "amt": body.amount_minor, "cur": body.currency, "cat": body.category,
                 "desc": body.description, "ref": body.source_reference, "acc": body.settlement_account_id, "u": ctx.user.id}).scalar_one()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{body.party_name} already has a {body.direction} numbered {body.document_number}.") from None
    record_event(db, action="obligation.create", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="obligation", target_id=oid,
                 details={"direction": body.direction, "document_number": body.document_number, "party": body.party_name,
                          "amount_minor": body.amount_minor, "currency": body.currency, "due_date": body.due_date.isoformat()})
    db.commit()
    return _detail(db, ctx.business_id, oid, today)


@router.post("/{obligation_id}/allocations", status_code=status.HTTP_201_CREATED)
def allocate(obligation_id: UUID, body: AllocationIn, ctx: BusinessContext = Depends(require("obligations.write")),
             db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    ob, allocated = _lock(db, ctx.business_id, obligation_id)
    audit = dict(action="obligation.allocate", actor=_actor(ctx), business_id=ctx.business_id, target_type="obligation", target_id=obligation_id)
    if ob["cancelled_at"]:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A cancelled invoice or bill cannot receive payments.")
    if body.currency != ob["currency"]:
        db.rollback()
        raise _unprocessable("currency", f"the {ob['direction']} is in {ob['currency']}; no conversion is applied")
    if not EARLIEST <= body.allocation_date <= today:
        db.rollback()
        raise _unprocessable("allocation_date", "must be between 2000-01-01 and today")
    outstanding = ob["amount_minor"] - allocated
    if body.amount_minor > outstanding:
        db.rollback()
        record_event(db, outcome="conflict", details={"reason": "over-allocation", "requested_minor": body.amount_minor,
                                                     "outstanding_minor": outstanding}, **audit)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": f"Only {outstanding} minor units are outstanding; this payment would over-allocate.",
                                                       "outstanding_minor": outstanding})
    if body.ledger_txn_id is not None:
        txn = db.execute(text("SELECT id, business_date, amount_minor, currency FROM ledger_transaction WHERE business_id = :b AND id = :t FOR UPDATE"),
                         {"b": ctx.business_id, "t": body.ledger_txn_id}).mappings().first()
        if txn is None:
            db.rollback()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
        expected_sign = 1 if ob["direction"] == "receivable" else -1
        if txn["amount_minor"] * expected_sign <= 0:
            db.rollback()
            raise _unprocessable("ledger_txn_id", "a receivable is paid by money in; a payable by money out")
        if txn["currency"] != ob["currency"]:
            db.rollback()
            raise _unprocessable("ledger_txn_id", "the transaction is in a different currency")
        if txn["business_date"] != body.allocation_date:
            db.rollback()
            raise _unprocessable("allocation_date", f"must equal the transaction date {txn['business_date']}")
        used = int(db.execute(text(
            """SELECT coalesce(sum(a.amount_minor), 0) - coalesce((SELECT sum(-r.amount_minor) FROM allocation r
                   JOIN allocation p ON p.id = r.reverses_id WHERE p.ledger_txn_id = :t), 0)
               FROM allocation a WHERE a.ledger_txn_id = :t"""), {"t": body.ledger_txn_id}).scalar_one())  # sum() returns numeric
        if used + body.amount_minor > abs(txn["amount_minor"]):
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, {"message": f"The transaction has only {abs(txn['amount_minor']) - used} minor units left to allocate.",
                                                           "available_minor": abs(txn["amount_minor"]) - used})
    aid = db.execute(text(
        """INSERT INTO allocation (business_id, obligation_id, kind, amount_minor, currency, allocation_date, ledger_txn_id, note, created_by)
           VALUES (:b, :o, 'payment', :amt, :cur, :d, :t, :n, :u) RETURNING id"""),
        {"b": ctx.business_id, "o": obligation_id, "amt": body.amount_minor, "cur": body.currency, "d": body.allocation_date,
         "t": body.ledger_txn_id, "n": body.note, "u": ctx.user.id}).scalar_one()
    record_event(db, outcome="success", details={"allocation_id": str(aid), "amount_minor": body.amount_minor,
                                                 "outstanding_after_minor": outstanding - body.amount_minor,
                                                 "linked_transaction": body.ledger_txn_id is not None}, **audit)
    db.commit()
    return _detail(db, ctx.business_id, obligation_id, today)


@router.post("/{obligation_id}/allocations/{allocation_id}/reverse", status_code=status.HTTP_201_CREATED)
def reverse(obligation_id: UUID, allocation_id: UUID, body: ReversalIn, ctx: BusinessContext = Depends(require("obligations.write")),
            db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    ob, _ = _lock(db, ctx.business_id, obligation_id)
    a = db.execute(text("SELECT * FROM allocation WHERE obligation_id = :o AND id = :a"), {"o": obligation_id, "a": allocation_id}).mappings().first()
    if a is None:
        db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found on this invoice or bill")
    if a["kind"] != "payment":
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a payment can be reversed.")
    if not a["allocation_date"] <= body.reversal_date <= today:
        db.rollback()
        raise _unprocessable("reversal_date", f"must be between the payment date {a['allocation_date']} and today")
    try:
        with db.begin_nested():
            rid = db.execute(text(
                """INSERT INTO allocation (business_id, obligation_id, kind, amount_minor, currency, allocation_date, reverses_id, note, created_by)
                   VALUES (:b, :o, 'reversal', :amt, :cur, :d, :r, :n, :u) RETURNING id"""),
                {"b": ctx.business_id, "o": obligation_id, "amt": -a["amount_minor"], "cur": a["currency"], "d": body.reversal_date,
                 "r": allocation_id, "n": body.reason, "u": ctx.user.id}).scalar_one()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This payment has already been reversed.") from None
    record_event(db, action="obligation.reverse", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="obligation", target_id=obligation_id,
                 details={"reversal_id": str(rid), "reversed_allocation_id": str(allocation_id), "amount_minor": a["amount_minor"],
                          "reason": body.reason})
    db.commit()
    return _detail(db, ctx.business_id, obligation_id, today)


@router.post("/{obligation_id}/dispute")
def dispute(obligation_id: UUID, body: DisputeIn, ctx: BusinessContext = Depends(require("obligations.write")),
            db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    ob, _ = _lock(db, ctx.business_id, obligation_id)
    if ob["cancelled_at"]:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A cancelled invoice or bill cannot be disputed.")
    if ob["disputed"] == body.disputed:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "It is already " + ("disputed." if body.disputed else "not disputed."))
    db.execute(text("UPDATE obligation SET disputed = :d, dispute_reason = :r, disputed_at = CASE WHEN :d THEN now() END WHERE id = :o"),
               {"d": body.disputed, "r": body.reason, "o": obligation_id})
    record_event(db, action="obligation.dispute", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="obligation", target_id=obligation_id, details={"disputed": body.disputed, "reason": body.reason})
    db.commit()
    return _detail(db, ctx.business_id, obligation_id, today)


@router.post("/{obligation_id}/cancel")
def cancel(obligation_id: UUID, body: CancelIn, ctx: BusinessContext = Depends(require("obligations.write")),
           db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    ob, allocated = _lock(db, ctx.business_id, obligation_id)
    if ob["cancelled_at"]:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "It is already cancelled.")
    if allocated != 0:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "It has net payments allocated. Reverse them before cancelling.")
    db.execute(text("UPDATE obligation SET cancelled_at = now(), cancelled_by = :u, cancel_reason = :r WHERE id = :o"),
               {"u": ctx.user.id, "r": body.reason, "o": obligation_id})
    record_event(db, action="obligation.cancel", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="obligation", target_id=obligation_id, details={"reason": body.reason})
    db.commit()
    return _detail(db, ctx.business_id, obligation_id, today)

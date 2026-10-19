"""Cash reconstruction API: position, anchors, coverage, restricted funds, and own-account transfers."""

from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import check_currency, clean_text
from app.modules.cash.service import TRANSFER_LINK_MAX_DAYS, load_inputs, reconstruct
from app.modules.identity.deps import BusinessContext, require
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/cash", tags=["cash reconstruction"])
EARLIEST = date(2000, 1, 1)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnchorIn(_Strict):
    account_id: UUID
    anchor_date: date
    balance_minor: StrictInt = Field(ge=-MAX_AMOUNT_MINOR, le=MAX_AMOUNT_MINOR)
    currency: StrictStr
    source_kind: Literal["bank_statement", "wallet_statement", "cash_count", "opening_balance"]
    source_reference: StrictStr

    validate_currency = field_validator("currency")(check_currency)
    validate_reference = field_validator("source_reference")(lambda v: clean_text(v, 120))


class CoverageIn(_Strict):
    account_id: UUID
    period_start: date
    period_end: date
    source_kind: Literal["import_batch", "statement", "manual_confirmation"]
    import_batch_id: UUID | None = None
    note: StrictStr | None = None

    validate_note = field_validator("note")(lambda v: clean_text(v, 200) if v is not None else v)

    @model_validator(mode="after")
    def _shape(self) -> "CoverageIn":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        if (self.period_end - self.period_start).days > 366:
            raise ValueError("a coverage period can span at most 367 days")
        if (self.source_kind == "import_batch") != (self.import_batch_id is not None):
            raise ValueError("import_batch_id is required for source_kind import_batch and not allowed otherwise")
        return self


class RestrictionIn(_Strict):
    account_id: UUID
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    currency: StrictStr
    reason: StrictStr
    effective_from: date
    effective_to: date | None = None

    validate_currency = field_validator("currency")(check_currency)
    validate_reason = field_validator("reason")(lambda v: clean_text(v, 200))

    @model_validator(mode="after")
    def _dates(self) -> "RestrictionIn":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        return self


class TransferIn(_Strict):
    out_txn_id: UUID | None = None
    in_txn_id: UUID | None = None
    note: StrictStr | None = None

    validate_note = field_validator("note")(lambda v: clean_text(v, 200) if v is not None else v)

    @model_validator(mode="after")
    def _sides(self) -> "TransferIn":
        if self.out_txn_id is None and self.in_txn_id is None:
            raise ValueError("give the outgoing transaction, the incoming transaction, or both")
        if self.out_txn_id is not None and self.out_txn_id == self.in_txn_id:
            raise ValueError("the two sides must be different transactions")
        return self


class VoidIn(_Strict):
    reason: StrictStr

    validate_reason = field_validator("reason")(lambda v: clean_text(v, 200))


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _unprocessable(field_name: str, msg: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", field_name], "msg": msg, "type": "value_error"}])


def _today(db: Session, business_id: UUID) -> date:
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one()
    return business_today(tz)


def _account(db: Session, business_id: UUID, account_id: UUID):
    acc = db.execute(text("SELECT id, label, currency FROM cash_account WHERE business_id = :b AND id = :a"),
                     {"b": business_id, "a": account_id}).mappings().first()
    if acc is None:  # includes an account of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return acc


def _position(db: Session, business_id: UUID, as_of: date) -> dict:
    accounts, transfers = load_inputs(db, business_id)
    return reconstruct(accounts, transfers, as_of)


@router.get("/position")
def position(as_of: date | None = Query(None), ctx: BusinessContext = Depends(require("records.read")),
             db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    if as_of is not None and not EARLIEST <= as_of <= today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            [{"loc": ["query", "as_of"], "msg": "as_of must be between 2000-01-01 and today in the business timezone", "type": "value_error"}])
    return _position(db, ctx.business_id, as_of or today)


@router.post("/anchors", status_code=status.HTTP_201_CREATED)
def add_anchor(body: AnchorIn, ctx: BusinessContext = Depends(require("cash.manage")), db: Session = Depends(get_session)) -> dict:
    acc = _account(db, ctx.business_id, body.account_id)
    today = _today(db, ctx.business_id)
    if body.currency != acc["currency"]:
        raise _unprocessable("currency", f"the account is in {acc['currency']}; balances are never converted")
    if not EARLIEST <= body.anchor_date <= today:
        raise _unprocessable("anchor_date", "must be between 2000-01-01 and today in the business timezone")
    try:
        with db.begin_nested():
            anchor_id = db.execute(text(
                """INSERT INTO balance_anchor (business_id, account_id, anchor_date, balance_minor, currency, source_kind,
                                               source_reference, entered_by)
                   VALUES (:b, :a, :d, :bal, :c, :k, :ref, :u) RETURNING id"""),
                {"b": ctx.business_id, "a": body.account_id, "d": body.anchor_date, "bal": body.balance_minor, "c": body.currency,
                 "k": body.source_kind, "ref": body.source_reference, "u": ctx.user.id}).scalar_one()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account already has a balance for that date. Void it first to replace it.") from None
    record_event(db, action="cash.anchor_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="balance_anchor", target_id=anchor_id,
                 details={"account": acc["label"], "anchor_date": body.anchor_date.isoformat(), "balance_minor": body.balance_minor,
                          "source_kind": body.source_kind})
    db.commit()
    return _position(db, ctx.business_id, today) | {"created_id": str(anchor_id)}


@router.post("/coverage", status_code=status.HTTP_201_CREATED)
def add_coverage(body: CoverageIn, ctx: BusinessContext = Depends(require("cash.manage")), db: Session = Depends(get_session)) -> dict:
    acc = _account(db, ctx.business_id, body.account_id)
    today = _today(db, ctx.business_id)
    if body.period_end > today:
        raise _unprocessable("period_end", "coverage cannot extend past today in the business timezone")
    if body.import_batch_id is not None:
        batch = db.execute(text("SELECT account_id, status FROM import_batch WHERE business_id = :b AND id = :i"),
                           {"b": ctx.business_id, "i": body.import_batch_id}).mappings().first()
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Import not found")
        if batch["account_id"] != body.account_id:
            raise _unprocessable("import_batch_id", "the import belongs to a different account")
        if batch["status"] != "posted":
            raise _unprocessable("import_batch_id", "only a posted import can back a coverage period")
        span = db.execute(text("SELECT min(business_date), max(business_date) FROM ledger_transaction WHERE source_batch_id = :i"),
                          {"i": body.import_batch_id}).one()
        if span[0] and (span[0] < body.period_start or span[1] > body.period_end):
            raise _unprocessable("period_start", f"the import's posted rows run from {span[0]} to {span[1]}; the period must include them")
    cov_id = db.execute(text(
        """INSERT INTO coverage_period (business_id, account_id, period_start, period_end, source_kind, import_batch_id, note, created_by)
           VALUES (:b, :a, :s, :e, :k, :i, :n, :u) RETURNING id"""),
        {"b": ctx.business_id, "a": body.account_id, "s": body.period_start, "e": body.period_end, "k": body.source_kind,
         "i": body.import_batch_id, "n": body.note, "u": ctx.user.id}).scalar_one()
    record_event(db, action="cash.coverage_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="coverage_period", target_id=cov_id,
                 details={"account": acc["label"], "period_start": body.period_start.isoformat(),
                          "period_end": body.period_end.isoformat(), "source_kind": body.source_kind})
    db.commit()
    return _position(db, ctx.business_id, today) | {"created_id": str(cov_id)}


@router.post("/restrictions", status_code=status.HTTP_201_CREATED)
def add_restriction(body: RestrictionIn, ctx: BusinessContext = Depends(require("cash.manage")), db: Session = Depends(get_session)) -> dict:
    acc = _account(db, ctx.business_id, body.account_id)
    today = _today(db, ctx.business_id)
    if body.currency != acc["currency"]:
        raise _unprocessable("currency", f"the account is in {acc['currency']}")
    if not EARLIEST <= body.effective_from <= today + timedelta(days=366):
        raise _unprocessable("effective_from", "must be between 2000-01-01 and one year from today")
    rid = db.execute(text(
        """INSERT INTO restricted_fund (business_id, account_id, amount_minor, currency, reason, effective_from, effective_to, created_by)
           VALUES (:b, :a, :amt, :c, :r, :f, :t, :u) RETURNING id"""),
        {"b": ctx.business_id, "a": body.account_id, "amt": body.amount_minor, "c": body.currency, "r": body.reason,
         "f": body.effective_from, "t": body.effective_to, "u": ctx.user.id}).scalar_one()
    record_event(db, action="cash.restriction_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="restricted_fund", target_id=rid,
                 details={"account": acc["label"], "amount_minor": body.amount_minor, "effective_from": body.effective_from.isoformat()})
    db.commit()
    return _position(db, ctx.business_id, today) | {"created_id": str(rid)}


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
def add_transfer(body: TransferIn, ctx: BusinessContext = Depends(require("cash.manage")), db: Session = Depends(get_session)) -> dict:
    def txn(txn_id: UUID | None, field_name: str):
        if txn_id is None:
            return None
        t = db.execute(text("SELECT id, account_id, business_date, amount_minor, currency FROM ledger_transaction WHERE business_id = :b AND id = :t"),
                       {"b": ctx.business_id, "t": txn_id}).mappings().first()
        if t is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
        return t

    out_t, in_t = txn(body.out_txn_id, "out_txn_id"), txn(body.in_txn_id, "in_txn_id")
    if out_t is not None and out_t["amount_minor"] >= 0:
        raise _unprocessable("out_txn_id", "the outgoing side must be money out (a negative amount)")
    if in_t is not None and in_t["amount_minor"] <= 0:
        raise _unprocessable("in_txn_id", "the incoming side must be money in (a positive amount)")
    if out_t is not None and in_t is not None:
        if out_t["account_id"] == in_t["account_id"]:
            raise _unprocessable("in_txn_id", "a transfer must move money between two different accounts")
        if out_t["currency"] != in_t["currency"]:
            raise _unprocessable("in_txn_id", "both sides must be in the same currency; no conversion is applied")
        if -out_t["amount_minor"] != in_t["amount_minor"]:
            raise _unprocessable("in_txn_id", "both sides must have the same amount")
        if abs((in_t["business_date"] - out_t["business_date"]).days) > TRANSFER_LINK_MAX_DAYS:
            raise _unprocessable("in_txn_id", f"the two sides are more than {TRANSFER_LINK_MAX_DAYS} days apart")
    try:
        with db.begin_nested():
            tid = db.execute(text(
                "INSERT INTO own_transfer (business_id, out_txn_id, in_txn_id, note, created_by) VALUES (:b, :o, :i, :n, :u) RETURNING id"),
                {"b": ctx.business_id, "o": body.out_txn_id, "i": body.in_txn_id, "n": body.note, "u": ctx.user.id}).scalar_one()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "One of these transactions is already part of a transfer. Void that transfer first.") from None
    side = out_t or in_t
    record_event(db, action="cash.transfer_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="own_transfer", target_id=tid,
                 details={"matched": bool(out_t and in_t), "amount_minor": abs(side["amount_minor"])})
    db.commit()
    return _position(db, ctx.business_id, _today(db, ctx.business_id)) | {"created_id": str(tid)}


VOIDABLE = {"anchors": "balance_anchor", "coverage": "coverage_period", "restrictions": "restricted_fund", "transfers": "own_transfer"}


@router.post("/{kind}/{item_id}/void")
def void_item(kind: Literal["anchors", "coverage", "restrictions", "transfers"], item_id: UUID, body: VoidIn,
              ctx: BusinessContext = Depends(require("cash.manage")), db: Session = Depends(get_session)) -> dict:
    table = VOIDABLE[kind]
    n = db.execute(text(f"UPDATE {table} SET voided_at = now(), voided_by = :u, void_reason = :r "
                        f"WHERE business_id = :b AND id = :i AND voided_at IS NULL"),
                   {"u": ctx.user.id, "r": body.reason, "b": ctx.business_id, "i": item_id}).rowcount
    if n == 0:
        exists = db.execute(text(f"SELECT 1 FROM {table} WHERE business_id = :b AND id = :i"), {"b": ctx.business_id, "i": item_id}).first()
        db.rollback()
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "This entry is already voided.")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    record_event(db, action=f"cash.{table}_void", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type=table, target_id=item_id, details={"reason": body.reason})
    db.commit()
    return _position(db, ctx.business_id, _today(db, ctx.business_id))

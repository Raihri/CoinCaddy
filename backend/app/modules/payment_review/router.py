"""Payment Review API: run four scenarios on one frozen snapshot and save the report. Never initiates a payment."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import clean_text
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.service import canonical, freeze_inputs, sha256
from app.modules.payment_review.engine import CALC_VERSION, ReviewError, review
from app.modules.records.normalize import looks_like_formula
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/payment-reviews", tags=["payment review"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdverseIn(_Strict):
    receipt_delay_days: StrictInt = Field(0, ge=0, le=90)
    receipts_haircut_percent: StrictInt = Field(0, ge=0, le=100)
    extra_outflow_minor: StrictInt = Field(0, ge=0, le=MAX_AMOUNT_MINOR)
    extra_outflow_date: date | None = None


class PaymentReviewIn(_Strict):
    account_id: UUID
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    payment_date: date
    linked_payable_id: UUID | None = None
    payee: StrictStr | None = None
    adverse: AdverseIn

    @field_validator("payee")
    @classmethod
    def _payee(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = clean_text(v, 80)
        if looks_like_formula(v):
            raise ValueError("must not look like a spreadsheet formula")
        return v


def _tz(db: Session, business_id: UUID) -> str:
    return db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one()


def _row(r) -> dict:
    return {"id": str(r["id"]), "calc_version": r["calc_version"], "as_of": r["as_of"].isoformat(), "request": r["request"],
            "inputs_sha256": r["inputs_sha256"], "results": r["results"], "decision": r["decision"],
            "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat()}


_SELECT = "SELECT p.*, u.display_name AS created_by_name FROM payment_review p JOIN app_user u ON u.id = p.created_by WHERE p.business_id = :b"


@router.post("", status_code=status.HTTP_201_CREATED)
def create(body: PaymentReviewIn, ctx: BusinessContext = Depends(require("paymentreview.run")), db: Session = Depends(get_session)) -> dict:
    inputs = freeze_inputs(db, ctx.business_id, business_today(_tz(db, ctx.business_id)))
    req = {"account_id": str(body.account_id), "amount_minor": body.amount_minor, "payment_date": body.payment_date.isoformat(),
           "linked_payable_id": str(body.linked_payable_id) if body.linked_payable_id else None, "payee": body.payee,
           "adverse": {"receipt_delay_days": body.adverse.receipt_delay_days, "receipts_haircut_percent": body.adverse.receipts_haircut_percent,
                       "extra_outflow_minor": body.adverse.extra_outflow_minor,
                       "extra_outflow_date": body.adverse.extra_outflow_date.isoformat() if body.adverse.extra_outflow_date else None}}
    try:
        results = review(inputs, req)
    except ReviewError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rid = db.execute(text(
        """INSERT INTO payment_review (business_id, calc_version, as_of, request, inputs, inputs_sha256, results, decision, created_by)
           VALUES (:b, :v, :d, CAST(:rq AS jsonb), CAST(:i AS jsonb), :h, CAST(:r AS jsonb), :dec, :u) RETURNING id"""),
        {"b": ctx.business_id, "v": CALC_VERSION, "d": inputs["as_of"], "rq": canonical(req), "i": canonical(inputs), "h": sha256(inputs),
         "r": canonical(results), "dec": results["decision"], "u": ctx.user.id}).scalar_one()
    record_event(db, action="paymentreview.create", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role),
                 business_id=ctx.business_id, target_type="payment_review", target_id=rid,
                 details={"decision": results["decision"], "amount_minor": body.amount_minor, "currency": results["currency"],
                          "linked_payable": body.linked_payable_id is not None, "inputs_sha256": sha256(inputs)[:16]})
    db.commit()
    return _row(db.execute(text(_SELECT + " AND p.id = :i"), {"b": ctx.business_id, "i": rid}).mappings().one())


@router.get("")
def list_reviews(ctx: BusinessContext = Depends(require("paymentreview.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(text(_SELECT + " ORDER BY p.created_at DESC LIMIT 50"), {"b": ctx.business_id}).mappings().all()
    return [{k: v for k, v in _row(r).items() if k != "results"} | {"payment": r["results"]["payment"], "currency": r["results"]["currency"]} for r in rows]


def _load(db: Session, ctx: BusinessContext, review_id: UUID):
    r = db.execute(text(_SELECT + " AND p.id = :i"), {"b": ctx.business_id, "i": review_id}).mappings().first()
    if r is None:  # includes a report of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment review not found")
    return r


@router.get("/{review_id}")
def get_review(review_id: UUID, ctx: BusinessContext = Depends(require("paymentreview.read")), db: Session = Depends(get_session)) -> dict:
    return _row(_load(db, ctx, review_id))


@router.get("/{review_id}/verify")
def verify(review_id: UUID, ctx: BusinessContext = Depends(require("paymentreview.read")), db: Session = Depends(get_session)) -> dict:
    r = _load(db, ctx, review_id)
    return {"review_id": str(r["id"]), "calc_version": r["calc_version"], "inputs_hash_matches": sha256(r["inputs"]) == r["inputs_sha256"],
            "results_reproduce": canonical(review(r["inputs"], r["request"])) == canonical(r["results"])}

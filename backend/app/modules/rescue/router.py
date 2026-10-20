"""Cash Gap Rescue Plan API: documented deferral permissions, and rescue plans for a saved Payment Review.

A plan is a set of proposals. Nothing here changes the ledger, an invoice, a bill, or any external system.
"""

from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import clean_text
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, sha256
from app.modules.records.normalize import looks_like_formula
from app.modules.records.service import business_today
from app.modules.rescue.engine import CALC_VERSION, DISCRETIONARY, PlanError, plan

router = APIRouter(prefix="/api/businesses/{business_id}/payment-reviews/{review_id}/rescue-plans", tags=["rescue plan"])
permission_router = APIRouter(prefix="/api/businesses/{business_id}/obligations/{obligation_id}/deferral-permissions", tags=["rescue plan"])
MAX_DEFERRAL_DAYS = 90


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


class PermissionIn(_Strict):
    agreed_due_date: date
    evidence_reference: StrictStr
    note: StrictStr | None = None
    validate_ref = field_validator("evidence_reference")(_text(120))
    validate_note = field_validator("note")(_text(200))


class VoidIn(_Strict):
    reason: StrictStr
    validate_reason = field_validator("reason")(_text(200))


class PlanIn(_Strict):
    path: Literal["on_time", "receipts_delayed", "largest_missed", "adverse"] | None = None
    collect_obligation_id: UUID | None = None
    collect_date: date | None = None
    collect_percent: StrictInt | None = Field(None, ge=1, le=100)
    defer_obligation_id: UUID | None = None
    deferral_date: date | None = None
    financing_product_id: StrictStr | None = Field(None, max_length=40)


def _perm(r) -> dict:
    return {"id": str(r["id"]), "obligation_id": str(r["obligation_id"]), "agreed_due_date": r["agreed_due_date"].isoformat(),
            "evidence_reference": r["evidence_reference"], "note": r["note"], "created_at": r["created_at"].isoformat(),
            "created_by": r["created_by_name"], "voided": r["voided_at"] is not None, "void_reason": r["void_reason"]}


_PSEL = """SELECT d.*, u.display_name AS created_by_name FROM deferral_permission d JOIN app_user u ON u.id = d.created_by
          WHERE d.business_id = :b AND d.obligation_id = :o"""


def _obligation(db: Session, ctx: BusinessContext, obligation_id: UUID, today: date) -> dict:
    ob = next(iter(list_obligations(db, ctx.business_id, today, obligation_id=obligation_id)), None)
    if ob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice or bill not found")
    return ob


def _today(db: Session, business_id: UUID) -> date:
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


@permission_router.get("")
def list_permissions(obligation_id: UUID, ctx: BusinessContext = Depends(require("obligations.read")), db: Session = Depends(get_session)) -> list[dict]:
    _obligation(db, ctx, obligation_id, _today(db, ctx.business_id))
    return [_perm(r) for r in db.execute(text(_PSEL + " ORDER BY d.created_at DESC"), {"b": ctx.business_id, "o": obligation_id}).mappings().all()]


@permission_router.post("", status_code=status.HTTP_201_CREATED)
def add_permission(obligation_id: UUID, body: PermissionIn, ctx: BusinessContext = Depends(require("obligations.write")), db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    ob = _obligation(db, ctx, obligation_id, today)
    if ob["direction"] != "payable" or ob["status"] not in ("open", "partially_settled"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A payment permission can only be recorded for an outstanding bill.")
    if ob["category"] not in DISCRETIONARY:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only discretionary bills (marketing or other expense) can be moved. Rent, salaries, tax, loan repayments, and inventory cannot.")
    due = date.fromisoformat(ob["due_date"])
    if not due < body.agreed_due_date <= due + timedelta(days=MAX_DEFERRAL_DAYS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"The agreed date must be after the current due date ({ob['due_date']}) and within {MAX_DEFERRAL_DAYS} days of it.")
    pid = db.execute(text(
        """INSERT INTO deferral_permission (business_id, obligation_id, agreed_due_date, evidence_reference, note, created_by)
           VALUES (:b, :o, :d, :r, :n, :u) RETURNING id"""),
        {"b": ctx.business_id, "o": obligation_id, "d": body.agreed_due_date, "r": body.evidence_reference, "n": body.note, "u": ctx.user.id}).scalar_one()
    record_event(db, action="obligation.deferral_permit", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role), business_id=ctx.business_id,
                 target_type="obligation", target_id=obligation_id, details={"agreed_due_date": body.agreed_due_date.isoformat(), "reference": body.evidence_reference})
    db.commit()
    return _perm(db.execute(text(_PSEL + " AND d.id = :i"), {"b": ctx.business_id, "o": obligation_id, "i": pid}).mappings().one())


@permission_router.post("/{permission_id}/void")
def void_permission(obligation_id: UUID, permission_id: UUID, body: VoidIn, ctx: BusinessContext = Depends(require("obligations.write")),
                    db: Session = Depends(get_session)) -> dict:
    row = db.execute(text(_PSEL + " AND d.id = :i FOR UPDATE OF d"), {"b": ctx.business_id, "o": obligation_id, "i": permission_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permission not found")
    if row["voided_at"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "That permission is already voided.")
    db.execute(text("UPDATE deferral_permission SET voided_at = now(), voided_by = :u, void_reason = :r WHERE id = :i"), {"u": ctx.user.id, "r": body.reason, "i": permission_id})
    record_event(db, action="obligation.deferral_void", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role), business_id=ctx.business_id,
                 target_type="obligation", target_id=obligation_id, details={"reason": body.reason})
    db.commit()
    return _perm(db.execute(text(_PSEL + " AND d.id = :i"), {"b": ctx.business_id, "o": obligation_id, "i": permission_id}).mappings().one())


# --- rescue plans ---------------------------------------------------------------------------------------


def _review(db: Session, ctx: BusinessContext, review_id: UUID):
    r = db.execute(text("SELECT id, request, inputs, results FROM payment_review WHERE business_id = :b AND id = :i"), {"b": ctx.business_id, "i": review_id}).mappings().first()
    if r is None:  # includes a review of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment review not found")
    return r


def _funding(db: Session, business_id: UUID, currency: str) -> dict | None:
    r = db.execute(text("SELECT id, request, catalogue, results, created_at FROM funding_match_run WHERE business_id = :b AND request->>'currency' = :c ORDER BY created_at DESC LIMIT 1"),
                   {"b": business_id, "c": currency}).mappings().first()
    if r is None:
        return None
    ids = [p["id"] for p in r["results"]["eligible"]]
    return {"run_id": str(r["id"]), "catalogue_version": r["catalogue"]["version"], "saved_at": r["created_at"].isoformat(), "amount_minor": r["request"]["amount_minor"],
            "receiving_account_id": r["request"]["receiving_account_id"], "eligible_ids": ids, "products": [p for p in r["catalogue"]["products"] if p["id"] in ids]}


def _build_inputs(db: Session, ctx: BusinessContext, rev, adjustments: dict) -> dict:
    cur = rev["results"]["currency"]
    meta = [{k: o[k] for k in ("id", "direction", "category", "currency", "due_date", "outstanding_minor", "disputed", "days_overdue", "document_number", "status")}
            | {"party": o["party"]["name"], "settlement_account_id": o["settlement_account"]["id"] if o["settlement_account"] else None}
            for o in list_obligations(db, ctx.business_id, date.fromisoformat(rev["inputs"]["as_of"])) if o["status"] in ("open", "partially_settled")]
    permits = {}
    for r in db.execute(text(
            """SELECT d.* FROM deferral_permission d WHERE d.business_id = :b AND d.voided_at IS NULL ORDER BY d.created_at"""), {"b": ctx.business_id}).mappings().all():
        permits[str(r["obligation_id"])] = {"id": str(r["id"]), "obligation_id": str(r["obligation_id"]), "agreed_due_date": r["agreed_due_date"].isoformat(),
                                            "evidence_reference": r["evidence_reference"]}
    return {"review_id": str(rev["id"]), "currency": cur, "outlook": rev["inputs"], "request": rev["request"], "obligations_meta": meta,
            "permissions": list(permits.values()), "funding": _funding(db, ctx.business_id, cur), "adjustments": adjustments}


def _row(r) -> dict:
    return {"id": str(r["id"]), "review_id": str(r["review_id"]), "calc_version": r["calc_version"], "as_of": r["as_of"].isoformat(), "adjustments": r["adjustments"],
            "inputs_sha256": r["inputs_sha256"], "results": r["results"], "recommendation": r["recommendation"],
            "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat()}


_SELECT = "SELECT p.*, u.display_name AS created_by_name FROM rescue_plan p JOIN app_user u ON u.id = p.created_by WHERE p.business_id = :b AND p.review_id = :r"


@router.post("", status_code=status.HTTP_201_CREATED)
def create(review_id: UUID, body: PlanIn, ctx: BusinessContext = Depends(require("rescue.run")), db: Session = Depends(get_session)) -> dict:
    rev = _review(db, ctx, review_id)
    adjustments = {k: (v.isoformat() if isinstance(v, date) else str(v) if isinstance(v, UUID) else v) for k, v in body.model_dump().items() if v is not None}
    inputs = _build_inputs(db, ctx, rev, adjustments)
    try:
        results = plan(inputs)
    except PlanError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    pid = db.execute(text(
        """INSERT INTO rescue_plan (business_id, review_id, calc_version, as_of, adjustments, inputs, inputs_sha256, results, recommendation, created_by)
           VALUES (:b, :r, :v, :d, CAST(:a AS jsonb), CAST(:i AS jsonb), :h, CAST(:res AS jsonb), :rec, :u) RETURNING id"""),
        {"b": ctx.business_id, "r": review_id, "v": CALC_VERSION, "d": results["as_of"], "a": canonical(adjustments), "i": canonical(inputs), "h": sha256(inputs),
         "res": canonical(results), "rec": results["recommendation"]["state"], "u": ctx.user.id}).scalar_one()
    record_event(db, action="rescue.run", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role), business_id=ctx.business_id,
                 target_type="rescue_plan", target_id=pid, details={"review_id": str(review_id), "recommendation": results["recommendation"]["state"],
                                                                    "recommended": results["recommendation"]["option_id"], "options": len(results["options"]),
                                                                    "inputs_sha256": sha256(inputs)[:16]})
    db.commit()
    return _row(db.execute(text(_SELECT + " AND p.id = :i"), {"b": ctx.business_id, "r": review_id, "i": pid}).mappings().one())


@router.get("")
def list_plans(review_id: UUID, ctx: BusinessContext = Depends(require("rescue.read")), db: Session = Depends(get_session)) -> list[dict]:
    _review(db, ctx, review_id)
    rows = db.execute(text(_SELECT + " ORDER BY p.created_at DESC LIMIT 50"), {"b": ctx.business_id, "r": review_id}).mappings().all()
    return [_row(r) for r in rows]


@router.get("/{plan_id}")
def get_plan(review_id: UUID, plan_id: UUID, ctx: BusinessContext = Depends(require("rescue.read")), db: Session = Depends(get_session)) -> dict:
    r = db.execute(text(_SELECT + " AND p.id = :i"), {"b": ctx.business_id, "r": review_id, "i": plan_id}).mappings().first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rescue plan not found")
    return _row(r)


@router.get("/{plan_id}/verify")
def verify(review_id: UUID, plan_id: UUID, ctx: BusinessContext = Depends(require("rescue.read")), db: Session = Depends(get_session)) -> dict:
    r = db.execute(text(_SELECT + " AND p.id = :i"), {"b": ctx.business_id, "r": review_id, "i": plan_id}).mappings().first()
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rescue plan not found")
    return {"plan_id": str(r["id"]), "inputs_hash_matches": sha256(r["inputs"]) == r["inputs_sha256"], "results_reproduce": canonical(plan(r["inputs"])) == canonical(r["results"])}

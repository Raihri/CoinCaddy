"""Conditional funding plans. Receipt checks use only posted ledger transactions linked to invoice allocations."""

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
from app.modules.conditional_funding import engine
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.engine import compute as compute_outlook
from app.modules.outlook.service import canonical, freeze_inputs, sha256
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/conditional-funding-plans", tags=["conditional funding plan"])
HORIZON_DAYS = 91


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanIn(_Strict):
    name: StrictStr = Field(min_length=1, max_length=100)
    obligation_id: UUID
    expected_amount_minor: StrictInt = Field(gt=0, le=1_000_000_000_000_000)
    trigger_date: date
    funding_match_run_id: UUID
    product_id: StrictStr = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        clean = clean_text(value, 100)
        if not clean:
            raise ValueError("Enter a plan name.")
        return clean


class ReviewIn(_Strict):
    decision: Literal["proceed", "decline"]
    note: StrictStr | None = Field(default=None, max_length=200)

    @field_validator("note")
    @classmethod
    def note_text(cls, value: str | None) -> str | None:
        return clean_text(value, 200) if value else value


def _today(db: Session, business_id: UUID) -> date:
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one()
    return business_today(tz)


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _plan_row(db: Session, ctx: BusinessContext, plan_id: UUID, *, lock: bool = False):
    row = db.execute(text(
        """SELECT p.*, o.document_number, party.name AS customer, f.request AS funding_request, f.catalogue AS funding_catalogue,
                  f.results AS funding_results
           FROM conditional_funding_plan p
           JOIN obligation o ON o.business_id = p.business_id AND o.id = p.obligation_id
           JOIN party ON party.business_id = o.business_id AND party.id = o.party_id
           JOIN funding_match_run f ON f.business_id = p.business_id AND f.id = p.funding_match_run_id
           WHERE p.business_id = :b AND p.id = :i""" + (" FOR UPDATE OF p" if lock else "")),
        {"b": ctx.business_id, "i": plan_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conditional funding plan not found")
    return row


def _events(db: Session, business_id: UUID, plan_id: UUID) -> list[dict]:
    rows = db.execute(text(
        """SELECT e.*, u.display_name AS actor_name FROM conditional_funding_event e JOIN app_user u ON u.id = e.actor_id
           WHERE e.business_id = :b AND e.plan_id = :p ORDER BY e.created_at, e.id"""), {"b": business_id, "p": plan_id}).mappings().all()
    return [{"type": r["event_type"], "details": r["details"], "actor": r["actor_name"], "at": r["created_at"].isoformat()} for r in rows]


def _snapshots(db: Session, business_id: UUID, plan_id: UUID) -> list[dict]:
    rows = db.execute(text(
        """SELECT id, generation, branch, calc_version, as_of, inputs_sha256, results, created_at
           FROM conditional_funding_snapshot WHERE business_id = :b AND plan_id = :p
           ORDER BY generation, branch"""), {"b": business_id, "p": plan_id}).mappings().all()
    return [{"id": str(r["id"]), "generation": r["generation"], "branch": r["branch"], "calc_version": r["calc_version"],
             "as_of": r["as_of"].isoformat(), "inputs_sha256": r["inputs_sha256"], "results": r["results"],
             "created_at": r["created_at"].isoformat()} for r in rows]


def _state(events: list[dict]) -> tuple[str, str | None]:
    trigger = next((e for e in events if e["type"] in ("receipt_verified", "trigger_missed")), None)
    recalc = next((e for e in reversed(events) if e["type"] == "cash_recalculated"), None)
    review = next((e for e in reversed(events) if e["type"] == "backup_reviewed"), None)
    if review:
        return "backup_reviewed", review["details"].get("decision")
    if recalc and trigger and trigger["type"] == "trigger_missed":
        return "backup_review_pending", None
    if recalc and trigger and trigger["type"] == "receipt_verified":
        return "receipt_recalculated", None
    if trigger:
        return ("receipt_verified" if trigger["type"] == "receipt_verified" else "trigger_missed"), None
    return "awaiting_receipt", None


def _view(db: Session, ctx: BusinessContext, row) -> dict:
    events = _events(db, ctx.business_id, row["id"])
    state_name, decision = _state(events)
    product = next((p for p in row["funding_catalogue"]["products"] if p["id"] == row["product_id"]), {})
    match_result = next((p for p in row["funding_results"]["eligible"] if p["id"] == row["product_id"]), {})
    return {"id": str(row["id"]), "name": row["name"], "state": state_name, "backup_decision": decision,
            "receipt": {"obligation_id": str(row["obligation_id"]), "document_number": row["document_number"], "customer": row["customer"],
                        "expected_amount_minor": row["expected_amount_minor"], "currency": row["currency"], "trigger_date": row["trigger_date"].isoformat()},
            "backup": {"funding_match_run_id": str(row["funding_match_run_id"]), "product_id": row["product_id"],
                       "name": product.get("name", row["product_id"]), "provider": product.get("provider", "Illustrative provider"),
                       "amount_minor": row["funding_request"]["amount_minor"], "currency": row["funding_request"]["currency"],
                       "terms": match_result.get("terms"), "label": "Illustrative match, not an approval"},
            "snapshots": _snapshots(db, ctx.business_id, row["id"]), "events": events,
            "created_at": row["created_at"].isoformat(), "created_by": str(row["created_by"]),
            "notice": "A forecast is an estimate. Only a posted transaction linked to a receivable allocation verifies settlement. A missed trigger creates no application or payment."}


def _save_snapshot(db: Session, *, ctx: BusinessContext, plan_id: UUID, generation: int, branch: str, as_of: date, inputs: dict, results: dict) -> str:
    sid = db.execute(text(
        """INSERT INTO conditional_funding_snapshot (business_id, plan_id, generation, branch, calc_version, as_of, inputs, inputs_sha256, results, created_by)
           VALUES (:b, :p, :g, :br, :v, :d, CAST(:i AS jsonb), :h, CAST(:r AS jsonb), :u) RETURNING id"""),
        {"b": ctx.business_id, "p": plan_id, "g": generation, "br": branch, "v": engine.CALC_VERSION, "d": as_of,
         "i": canonical(inputs), "h": sha256(inputs), "r": canonical(results), "u": ctx.user.id}).scalar_one()
    return str(sid)


def _event(db: Session, *, ctx: BusinessContext, plan_id: UUID, kind: str, details: dict) -> None:
    db.execute(text(
        """INSERT INTO conditional_funding_event (business_id, plan_id, event_type, details, actor_id)
           VALUES (:b, :p, :t, CAST(:d AS jsonb), :u)"""),
        {"b": ctx.business_id, "p": plan_id, "t": kind, "d": canonical(details), "u": ctx.user.id})


def _funding(row) -> tuple[dict, dict]:
    product = next((p for p in row["funding_catalogue"]["products"] if p["id"] == row["product_id"]), None)
    match = next((p for p in row["funding_results"]["eligible"] if p["id"] == row["product_id"]), None)
    if not product or not match:
        raise HTTPException(status.HTTP_409_CONFLICT, "The saved funding match no longer contains this eligible product.")
    return row["funding_request"], product


def _paths(db: Session, ctx: BusinessContext, row, today: date, generation: int, *, missed: bool = False,
           verified_amount_minor: int = 0) -> list[str]:
    frozen = freeze_inputs(db, ctx.business_id, today)
    req, product = _funding(row)
    receipt_date = date.fromisoformat(row["trigger_date"].isoformat())
    amount = max(0, int(row["expected_amount_minor"]) - verified_amount_minor) if missed else int(row["expected_amount_minor"])
    if missed:
        no_borrow = engine.no_borrow_case(frozen, obligation_id=str(row["obligation_id"]), expected_amount_minor=amount,
                                           receipt_date=receipt_date, as_of=today, include_receipt=False)
        disbursement = today + timedelta(days=max(1, int(product["funding_days"])))
        backup_inputs = {"outlook": frozen, "funding_request": req, "product_id": product["id"], "product": product,
                         "catalogue_version": row["funding_catalogue"]["version"], "expected_remaining_minor": amount,
                         "assumed_disbursement_date": disbursement.isoformat(), "assumption": "funds arrive after trigger miss"}
        backup = engine.backup_case(frozen, obligation_id=str(row["obligation_id"]), expected_amount_minor=amount, include_receipt=False,
                                    receipt_date=receipt_date, as_of=today, disbursement_date=disbursement,
                                    funding_run={"request": req}, product=product)
        ids = [
            _save_snapshot(db, ctx=ctx, plan_id=row["id"], generation=generation, branch="missed_no_borrow", as_of=today,
                           inputs={"outlook": frozen, "obligation_id": str(row["obligation_id"]), "expected_remaining_minor": amount,
                                   "trigger_date": receipt_date.isoformat(), "assumption": "expected receipt missed; no borrowing"}, results=no_borrow),
            _save_snapshot(db, ctx=ctx, plan_id=row["id"], generation=generation, branch="missed_with_backup", as_of=today,
                           inputs=backup_inputs, results=backup),
        ]
        return ids
    receipt_inputs = {"outlook": frozen, "obligation_id": str(row["obligation_id"]), "expected_amount_minor": amount,
                      "trigger_date": receipt_date.isoformat(), "assumption": "receipt arrives by trigger date"}
    receipt_outlook = engine.receipt_case(frozen, str(row["obligation_id"]), amount, receipt_date, include_receipt=True)
    from app.modules.funding.engine import _cash_view
    receipt_result = {"calc_version": engine.CALC_VERSION, "outlook_version": "outlook-v1", "currency": row["currency"],
                      "as_of": today.isoformat(), "forecast": _cash_view(compute_outlook(receipt_outlook), row["currency"]),
                      "note": "No financing assumed; the expected invoice amount is placed on the trigger date."}
    disbursement = receipt_date + timedelta(days=max(1, int(product["funding_days"])))
    backup_inputs = {"outlook": frozen, "obligation_id": str(row["obligation_id"]), "expected_amount_minor": amount,
                     "trigger_date": receipt_date.isoformat(), "funding_request": req, "product_id": product["id"], "product": product,
                     "catalogue_version": row["funding_catalogue"]["version"], "assumed_disbursement_date": disbursement.isoformat(),
                     "assumption": "receipt misses; illustrative backup funds after the trigger"}
    backup = engine.backup_case(frozen, obligation_id=str(row["obligation_id"]), expected_amount_minor=amount,
                                include_receipt=False, receipt_date=receipt_date, as_of=today, disbursement_date=disbursement,
                                funding_run={"request": req}, product=product)
    return [
        _save_snapshot(db, ctx=ctx, plan_id=row["id"], generation=generation, branch="receipt_expected", as_of=today,
                       inputs=receipt_inputs, results=receipt_result),
        _save_snapshot(db, ctx=ctx, plan_id=row["id"], generation=generation, branch="backup_if_missed", as_of=today,
                       inputs=backup_inputs, results=backup),
    ]


@router.get("")
def list_plans(ctx: BusinessContext = Depends(require("conditional.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(text("SELECT id FROM conditional_funding_plan WHERE business_id = :b ORDER BY created_at DESC LIMIT 100"),
                      {"b": ctx.business_id}).scalars().all()
    return [_view(db, ctx, _plan_row(db, ctx, row_id)) for row_id in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanIn, ctx: BusinessContext = Depends(require("conditional.write")), db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    if not today < body.trigger_date <= today + timedelta(days=HORIZON_DAYS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Choose a trigger date after today and within {HORIZON_DAYS} days.")
    db.execute(text("SELECT id FROM obligation WHERE business_id = :b AND id = :o FOR UPDATE"),
               {"b": ctx.business_id, "o": body.obligation_id})
    invoice = next(iter(list_obligations(db, ctx.business_id, today, obligation_id=body.obligation_id)), None)
    if invoice is None or invoice["direction"] != "receivable":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer invoice not found")
    if invoice["status"] not in ("open", "partially_settled") or invoice["disputed"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose an outstanding, undisputed customer invoice.")
    if body.expected_amount_minor > invoice["outstanding_minor"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Expected receipt cannot exceed the invoice's outstanding amount.")
    match_run = db.execute(text("SELECT * FROM funding_match_run WHERE business_id = :b AND id = :i"),
                           {"b": ctx.business_id, "i": body.funding_match_run_id}).mappings().first()
    if match_run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Saved funding match not found")
    if match_run["request"]["currency"] != invoice["currency"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The invoice and illustrative backup must use the same currency.")
    product = next((p for p in match_run["results"]["eligible"] if p["id"] == body.product_id), None)
    if product is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose an eligible product from the selected saved funding match.")
    active = db.execute(text(
        """SELECT p.id FROM conditional_funding_plan p WHERE p.business_id = :b AND p.obligation_id = :o
           AND NOT EXISTS (SELECT 1 FROM conditional_funding_event e WHERE e.plan_id = p.id AND e.event_type IN ('receipt_verified','trigger_missed')) LIMIT 1"""),
        {"b": ctx.business_id, "o": body.obligation_id}).scalar_one_or_none()
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice already has a plan awaiting its receipt trigger.")
    pid = db.execute(text(
        """INSERT INTO conditional_funding_plan (business_id, name, obligation_id, expected_amount_minor, currency, trigger_date,
                                                 funding_match_run_id, product_id, created_by)
           VALUES (:b, :n, :o, :a, :c, :d, :f, :p, :u) RETURNING id"""),
        {"b": ctx.business_id, "n": body.name, "o": body.obligation_id, "a": body.expected_amount_minor, "c": invoice["currency"],
         "d": body.trigger_date, "f": body.funding_match_run_id, "p": body.product_id, "u": ctx.user.id}).scalar_one()
    _event(db, ctx=ctx, plan_id=pid, kind="created", details={"trigger_date": body.trigger_date.isoformat(), "expected_amount_minor": body.expected_amount_minor,
                                                              "currency": invoice["currency"], "product_id": body.product_id})
    inserted = _plan_row(db, ctx, pid)
    snapshots = _paths(db, ctx, inserted, today, 1)
    record_event(db, action="conditional.create", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="conditional_funding_plan", target_id=pid,
                 details={"obligation_id": str(body.obligation_id), "expected_amount_minor": body.expected_amount_minor,
                          "currency": invoice["currency"], "trigger_date": body.trigger_date.isoformat(), "product_id": body.product_id})
    db.commit()
    return _view(db, ctx, _plan_row(db, ctx, pid))


@router.get("/{plan_id}")
def get_plan(plan_id: UUID, ctx: BusinessContext = Depends(require("conditional.read")), db: Session = Depends(get_session)) -> dict:
    return _view(db, ctx, _plan_row(db, ctx, plan_id))


@router.post("/{plan_id}/evaluate")
def evaluate(plan_id: UUID, ctx: BusinessContext = Depends(require("conditional.evaluate")), db: Session = Depends(get_session)) -> dict:
    row = _plan_row(db, ctx, plan_id, lock=True)
    current = _view(db, ctx, row)
    if current["state"] != "awaiting_receipt":
        return current | {"replayed": True}
    evidence = db.execute(text(
        """SELECT a.id AS allocation_id, a.ledger_txn_id, a.amount_minor, a.allocation_date, t.business_date
           FROM allocation a JOIN ledger_transaction t ON t.business_id = a.business_id AND t.id = a.ledger_txn_id
           WHERE a.business_id = :b AND a.obligation_id = :o AND a.kind = 'payment' AND a.ledger_txn_id IS NOT NULL
             AND a.created_at > :created_at
             AND NOT EXISTS (SELECT 1 FROM allocation r WHERE r.reverses_id = a.id)
           ORDER BY t.business_date, a.id"""), {"b": ctx.business_id, "o": row["obligation_id"], "created_at": row["created_at"]}).mappings().all()
    verified = sum(int(r["amount_minor"]) for r in evidence)
    today = _today(db, ctx.business_id)
    if verified >= int(row["expected_amount_minor"]):
        trigger_event, branch = "receipt_verified", "receipt"
        details = {"verified_amount_minor": verified, "expected_amount_minor": int(row["expected_amount_minor"]),
                   "source_allocations": [{"allocation_id": str(r["allocation_id"]), "transaction_id": str(r["ledger_txn_id"]),
                                           "business_date": r["business_date"].isoformat(), "amount_minor": int(r["amount_minor"])} for r in evidence]}
    elif today > row["trigger_date"]:
        trigger_event, branch = "trigger_missed", "missed"
        details = {"verified_amount_minor": verified, "expected_amount_minor": int(row["expected_amount_minor"]),
                   "trigger_date": row["trigger_date"].isoformat(), "notification_only": True}
    else:
        return current | {"replayed": False, "verified_amount_minor": verified,
                          "message": "Still waiting: no linked posted receipt has verified the expected amount, and the trigger date has not passed."}
    _event(db, ctx=ctx, plan_id=plan_id, kind=trigger_event, details=details)
    snapshot_ids = []
    if branch == "missed":
        snapshot_ids = _paths(db, ctx, row, today, 2, missed=True, verified_amount_minor=verified)
    else:
        frozen = freeze_inputs(db, ctx.business_id, today)
        result = {"calc_version": engine.CALC_VERSION, "outlook_version": "outlook-v1", "currency": row["currency"],
                  "as_of": today.isoformat(), "forecast": engine._cash_view(compute_outlook(frozen), row["currency"]),
                  "note": "Recalculated from the current posted ledger and outstanding invoice records."}
        snapshot_ids = [_save_snapshot(db, ctx=ctx, plan_id=plan_id, generation=2, branch="settled_actual", as_of=today,
                                       inputs={"outlook": frozen, "verified_receipt": details}, results=result)]
    _event(db, ctx=ctx, plan_id=plan_id, kind="cash_recalculated", details={"branch": branch, "snapshot_ids": snapshot_ids})
    record_event(db, action=f"conditional.{trigger_event}", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="conditional_funding_plan", target_id=plan_id,
                 details={"verified_amount_minor": verified, "expected_amount_minor": int(row["expected_amount_minor"]), "trigger_date": row["trigger_date"].isoformat()})
    db.commit()
    return _view(db, ctx, _plan_row(db, ctx, plan_id)) | {"replayed": False}


@router.post("/{plan_id}/review")
def review_backup(plan_id: UUID, body: ReviewIn, ctx: BusinessContext = Depends(require("conditional.review")), db: Session = Depends(get_session)) -> dict:
    row = _plan_row(db, ctx, plan_id, lock=True)
    view = _view(db, ctx, row)
    if view["state"] == "backup_reviewed":
        if view["backup_decision"] == body.decision:
            return view | {"replayed": True}
        raise HTTPException(status.HTTP_409_CONFLICT, "This backup review was already recorded with a different decision.")
    if view["state"] != "backup_review_pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "The backup is available for review only after a missed trigger and cash recalculation.")
    _event(db, ctx=ctx, plan_id=plan_id, kind="backup_reviewed", details={"decision": body.decision, "note": body.note,
                                                                           "submission": "none; continue through Funding Matches, readiness, and approvals if desired"})
    record_event(db, action="conditional.backup_review", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="conditional_funding_plan", target_id=plan_id, details={"decision": body.decision})
    db.commit()
    return _view(db, ctx, _plan_row(db, ctx, plan_id)) | {"replayed": False}

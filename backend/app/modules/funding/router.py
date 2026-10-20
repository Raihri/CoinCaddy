"""Funding Matches API: filter a versioned illustrative catalogue, cost eligible products, and place them in the forecast.

Nothing here applies to a lender, shares business data, or implies approval; results are saved, append-only runs.
"""

from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import check_currency
from app.modules.funding.engine import CALC_VERSION, DOCUMENTS, LABEL, NOTICE, PURPOSES, REVENUE_WINDOW_DAYS, SECURITY_DECLARED, MatchError, match
from app.modules.health_summary.service import load_rows
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.service import canonical, freeze_inputs, sha256
from app.modules.records.service import business_today
from app.modules.region_packs.registry import default_catalogue
from app.modules.region_packs.router import catalogue_for_business

router = APIRouter(prefix="/api/businesses/{business_id}/funding-matches", tags=["funding matches"])
# The default (Bangladesh) sample catalogue. A business reads the catalogue of its active region pack instead (see run() and catalogue()).
CATALOGUE = default_catalogue()


class MatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    currency: StrictStr
    needed_by: date
    purpose: Literal["working_capital", "inventory", "invoice_gap", "equipment", "other"]
    receiving_account_id: UUID
    months_trading: StrictInt = Field(ge=0, le=1200)
    security_available: list[Literal["guarantor", "collateral"]] = Field(default_factory=list, max_length=2)
    documents_available: list[Literal["bank_statements", "id", "trade_licence", "invoices", "wallet_history", "tax_certificate"]] = Field(default_factory=list, max_length=6)

    validate_currency = field_validator("currency")(check_currency)


def _inputs(db: Session, business_id: UUID, today: date, cur: str) -> dict:
    jur = db.execute(text("SELECT jurisdiction FROM business WHERE id = :b"), {"b": business_id}).scalar_one()
    outlook = freeze_inputs(db, business_id, today)
    revenue = sum(r.amount_minor for r in load_rows(db, business_id)
                  if r.kind == "operating_receipt" and r.currency == cur and today - timedelta(days=REVENUE_WINDOW_DAYS) < r.business_date <= today)
    receivables = sum(o["outstanding_minor"] for o in outlook["obligations"] if o["direction"] == "receivable" and o["currency"] == cur and not o["disputed"])
    return {"as_of": today.isoformat(), "jurisdiction": jur, "revenue_90d_minor": revenue, "receivables_eligible_minor": receivables, "outlook": outlook}


def _row(r) -> dict:
    return {"id": str(r["id"]), "calc_version": r["calc_version"], "catalogue_version": r["catalogue_version"], "as_of": r["as_of"].isoformat(),
            "request": r["request"], "inputs_sha256": r["inputs_sha256"], "results": r["results"],
            "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat()}


_SELECT = "SELECT f.*, u.display_name AS created_by_name FROM funding_match_run f JOIN app_user u ON u.id = f.created_by WHERE f.business_id = :b"


@router.get("/catalogue")
def catalogue(ctx: BusinessContext = Depends(require("funding.read")), db: Session = Depends(get_session)) -> dict:
    return catalogue_for_business(db, ctx.business_id) | {"label_for_results": LABEL, "notice": NOTICE, "purposes": PURPOSES, "documents": DOCUMENTS, "security_declared": SECURITY_DECLARED}


@router.post("", status_code=status.HTTP_201_CREATED)
def run(body: MatchIn, ctx: BusinessContext = Depends(require("funding.run")), db: Session = Depends(get_session)) -> dict:
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
    inputs = _inputs(db, ctx.business_id, business_today(tz), body.currency)
    req = {"amount_minor": body.amount_minor, "currency": body.currency, "needed_by": body.needed_by.isoformat(), "purpose": body.purpose,
           "receiving_account_id": str(body.receiving_account_id), "months_trading": body.months_trading,
           "security_available": sorted(set(body.security_available)), "documents_available": sorted(set(body.documents_available))}
    catalogue_used = catalogue_for_business(db, ctx.business_id)
    try:
        results = match(inputs, catalogue_used, req)
    except MatchError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    rid = db.execute(text(
        """INSERT INTO funding_match_run (business_id, calc_version, catalogue_version, as_of, request, inputs, inputs_sha256, catalogue, results, created_by)
           VALUES (:b, :v, :cv, :d, CAST(:rq AS jsonb), CAST(:i AS jsonb), :h, CAST(:c AS jsonb), CAST(:r AS jsonb), :u) RETURNING id"""),
        {"b": ctx.business_id, "v": CALC_VERSION, "cv": catalogue_used["version"], "d": inputs["as_of"], "rq": canonical(req), "i": canonical(inputs),
         "h": sha256(inputs), "c": canonical(catalogue_used), "r": canonical(results), "u": ctx.user.id}).scalar_one()
    record_event(db, action="funding.run", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role), business_id=ctx.business_id,
                 target_type="funding_match_run", target_id=rid,
                 details={"catalogue_version": catalogue_used["version"], "region_pack": (catalogue_used.get("region_pack") or {}).get("id"), "eligible": len(results["eligible"]), "excluded": len(results["excluded"]),
                          "amount_minor": body.amount_minor, "currency": body.currency, "inputs_sha256": sha256(inputs)[:16]})
    db.commit()
    return _row(db.execute(text(_SELECT + " AND f.id = :i"), {"b": ctx.business_id, "i": rid}).mappings().one())


@router.get("")
def list_runs(ctx: BusinessContext = Depends(require("funding.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(text(_SELECT + " ORDER BY f.created_at DESC LIMIT 50"), {"b": ctx.business_id}).mappings().all()
    return [{k: v for k, v in _row(r).items() if k != "results"} | {"eligible": len(r["results"]["eligible"]), "excluded": len(r["results"]["excluded"])} for r in rows]


def _load(db: Session, ctx: BusinessContext, run_id: UUID):
    r = db.execute(text(_SELECT + " AND f.id = :i"), {"b": ctx.business_id, "i": run_id}).mappings().first()
    if r is None:  # includes a run of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Funding match not found")
    return r


@router.get("/{run_id}")
def get_run(run_id: UUID, ctx: BusinessContext = Depends(require("funding.read")), db: Session = Depends(get_session)) -> dict:
    return _row(_load(db, ctx, run_id))


@router.get("/{run_id}/verify")
def verify(run_id: UUID, ctx: BusinessContext = Depends(require("funding.read")), db: Session = Depends(get_session)) -> dict:
    r = _load(db, ctx, run_id)
    return {"run_id": str(r["id"]), "inputs_hash_matches": sha256(r["inputs"]) == r["inputs_sha256"],
            "results_reproduce": canonical(match(r["inputs"], r["catalogue"], r["request"])) == canonical(r["results"])}

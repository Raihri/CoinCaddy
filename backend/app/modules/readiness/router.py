"""Financing Readiness API: the business document register, and readiness checks for one product of a saved Funding Match.

A check is a saved, append-only checklist. Nothing here applies to a lender, submits an application, or shares data.
"""

import re
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import clean_text
from app.modules.cash.service import load_inputs, reconstruct
from app.modules.health_summary.service import load_rows
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, sha256
from app.modules.readiness.engine import CALC_VERSION, WINDOW_DAYS, ReadinessError, assess
from app.modules.records.normalize import looks_like_formula
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/financing-readiness", tags=["financing readiness"])
documents_router = APIRouter(prefix="/api/businesses/{business_id}/business-documents", tags=["financing readiness"])
LONG_NUMBER = re.compile(r"\d{8}")


def _text(max_len: int, no_numbers: bool = False):
    def check(v: str | None) -> str | None:
        if v is None:
            return v
        v = clean_text(v, max_len)
        if looks_like_formula(v):
            raise ValueError("must not look like a spreadsheet formula")
        if no_numbers and LONG_NUMBER.search(re.sub(r"[\s-]", "", v)):
            raise ValueError("must not contain an ID, licence, or account number; say where the document is kept instead")
        return v
    return check


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentIn(_Strict):
    kind: Literal["id", "trade_licence", "tax_certificate", "guarantor_letter", "collateral_valuation"]
    reference: StrictStr
    issued_on: date | None = None
    expires_on: date | None = None
    validate_ref = field_validator("reference")(_text(80, no_numbers=True))

    @model_validator(mode="after")
    def dates(self):
        if self.issued_on and self.issued_on < date(1950, 1, 1):
            raise ValueError("issued_on is too early")
        if self.expires_on and self.expires_on < date(2000, 1, 1):
            raise ValueError("expires_on is too early")
        if self.issued_on and self.expires_on and self.expires_on <= self.issued_on:
            raise ValueError("expires_on must be after issued_on")
        return self


class VoidIn(_Strict):
    reason: StrictStr
    validate_reason = field_validator("reason")(_text(200))


class CheckIn(_Strict):
    run_id: UUID
    product_id: StrictStr = Field(min_length=1, max_length=40)


def _today(db: Session, business_id: UUID) -> date:
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


# --- document register ----------------------------------------------------------------------------------

_DSEL = """SELECT d.*, u.display_name AS created_by_name FROM business_document d JOIN app_user u ON u.id = d.created_by WHERE d.business_id = :b"""


def _doc(r) -> dict:
    return {"id": str(r["id"]), "kind": r["kind"], "reference": r["reference"], "issued_on": r["issued_on"].isoformat() if r["issued_on"] else None,
            "expires_on": r["expires_on"].isoformat() if r["expires_on"] else None, "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat(),
            "voided": r["voided_at"] is not None, "void_reason": r["void_reason"]}


@documents_router.get("")
def list_documents(ctx: BusinessContext = Depends(require("documents.read")), db: Session = Depends(get_session)) -> list[dict]:
    return [_doc(r) for r in db.execute(text(_DSEL + " ORDER BY d.voided_at IS NOT NULL, d.created_at DESC"), {"b": ctx.business_id}).mappings().all()]


@documents_router.post("", status_code=status.HTTP_201_CREATED)
def add_document(body: DocumentIn, ctx: BusinessContext = Depends(require("documents.write")), db: Session = Depends(get_session)) -> dict:
    today = _today(db, ctx.business_id)
    if body.issued_on and body.issued_on > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"The issue date cannot be after today ({today.isoformat()}).")
    did = db.execute(text(
        """INSERT INTO business_document (business_id, kind, reference, issued_on, expires_on, created_by)
           VALUES (:b, :k, :r, :i, :e, :u) RETURNING id"""),
        {"b": ctx.business_id, "k": body.kind, "r": body.reference, "i": body.issued_on, "e": body.expires_on, "u": ctx.user.id}).scalar_one()
    record_event(db, action="documents.write", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="business_document", target_id=did,
                 details={"kind": body.kind, "issued_on": body.issued_on.isoformat() if body.issued_on else None,
                          "expires_on": body.expires_on.isoformat() if body.expires_on else None})
    db.commit()
    return _doc(db.execute(text(_DSEL + " AND d.id = :i"), {"b": ctx.business_id, "i": did}).mappings().one())


@documents_router.post("/{document_id}/void")
def void_document(document_id: UUID, body: VoidIn, ctx: BusinessContext = Depends(require("documents.write")), db: Session = Depends(get_session)) -> dict:
    row = db.execute(text(_DSEL + " AND d.id = :i FOR UPDATE OF d"), {"b": ctx.business_id, "i": document_id}).mappings().first()
    if row is None:  # includes another business's document
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    if row["voided_at"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "That document entry is already voided.")
    db.execute(text("UPDATE business_document SET voided_at = now(), voided_by = :u, void_reason = :r WHERE id = :i"), {"u": ctx.user.id, "r": body.reason, "i": document_id})
    record_event(db, action="documents.void", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="business_document",
                 target_id=document_id, details={"kind": row["kind"], "reason": body.reason})
    db.commit()
    return _doc(db.execute(text(_DSEL + " AND d.id = :i"), {"b": ctx.business_id, "i": document_id}).mappings().one())


# --- readiness checks -----------------------------------------------------------------------------------


def _run(db: Session, ctx: BusinessContext, run_id: UUID):
    r = db.execute(text("SELECT id, request, catalogue FROM funding_match_run WHERE business_id = :b AND id = :i"), {"b": ctx.business_id, "i": run_id}).mappings().first()
    if r is None:  # includes a run of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Funding match not found")
    return r


def _build_inputs(db: Session, ctx: BusinessContext, run, product: dict, today: date) -> dict:
    b = ctx.business_id
    cur = run["request"]["currency"]
    start = today - timedelta(days=WINDOW_DAYS - 1)
    accounts_in, transfers = load_inputs(db, b)
    pos = reconstruct(accounts_in, transfers, today)
    txns = {a.id: a.txns for a in accounts_in}
    accounts = [{"id": a["account"]["id"], "label": a["account"]["label"], "kind": a["account"]["kind"], "currency": a["account"]["currency"],
                 "coverage": a["coverage"], "issue_codes": sorted({i["code"] for i in a["issues"]}),
                 "has_movements_in_window": any(start <= t.business_date <= today for t in txns[a["account"]["id"]])} for a in pos["accounts"]]
    rows = load_rows(db, b)
    win = [r for r in rows if r.currency == cur and start <= r.business_date <= today]
    receipts = [r for r in win if r.kind == "operating_receipt"]
    unclassified = [r for r in win if r.kind is None and r.amount_minor > 0]
    invoices = [o for o in list_obligations(db, b, today, direction="receivable")
                if o["status"] in ("open", "partially_settled") and o["currency"] == cur and not o["disputed"]]
    docs = [{"id": str(r["id"]), "kind": r["kind"], "reference": r["reference"], "issued_on": r["issued_on"].isoformat() if r["issued_on"] else None,
             "expires_on": r["expires_on"].isoformat() if r["expires_on"] else None, "recorded_by": r["created_by_name"], "recorded_at": r["created_at"].isoformat()}
            for r in db.execute(text(_DSEL + " AND d.voided_at IS NULL ORDER BY d.created_at"), {"b": b}).mappings().all()]
    return {
        "as_of": today.isoformat(),
        "jurisdiction": db.execute(text("SELECT jurisdiction FROM business WHERE id = :b"), {"b": b}).scalar_one(),
        "run": {"id": str(run["id"]), "catalogue_version": run["catalogue"]["version"], "catalogue_updated": run["catalogue"]["updated"], "request": run["request"]},
        "product": product,
        "records": {
            "accounts": accounts,
            "revenue": {"classified_receipts_minor": sum(r.amount_minor for r in receipts), "classified_count": len(receipts),
                        "unclassified_inflow_minor": sum(r.amount_minor for r in unclassified), "unclassified_inflow_count": len(unclassified)},
            "earliest_record_date": min(r.business_date for r in rows).isoformat() if rows else None,
            "receivables": {"eligible_minor": sum(o["outstanding_minor"] for o in invoices), "count": len(invoices),
                            "document_numbers": sorted(o["document_number"] for o in invoices)[:10]},
        },
        "documents": docs,
    }


def _row(r) -> dict:
    return {"id": str(r["id"]), "run_id": str(r["run_id"]), "product_id": r["product_id"], "calc_version": r["calc_version"], "as_of": r["as_of"].isoformat(),
            "inputs_sha256": r["inputs_sha256"], "results": r["results"], "state": r["state"],
            "created_by": r["created_by_name"], "created_at": r["created_at"].isoformat()}


_SELECT = "SELECT c.*, u.display_name AS created_by_name FROM readiness_check c JOIN app_user u ON u.id = c.created_by WHERE c.business_id = :b"


@router.post("", status_code=status.HTTP_201_CREATED)
def run_check(body: CheckIn, ctx: BusinessContext = Depends(require("readiness.run")), db: Session = Depends(get_session)) -> dict:
    run = _run(db, ctx, body.run_id)
    product = next((p for p in run["catalogue"]["products"] if p["id"] == body.product_id), None)
    if product is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That product is not in the catalogue of this funding comparison.")
    inputs = _build_inputs(db, ctx, run, product, _today(db, ctx.business_id))
    try:
        results = assess(inputs)
    except ReadinessError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    n = results["counts"]
    cid = db.execute(text(
        """INSERT INTO readiness_check (business_id, run_id, product_id, calc_version, as_of, inputs, inputs_sha256, results, state,
                                        present_count, missing_count, uncertain_count, created_by)
           VALUES (:b, :r, :p, :v, :d, CAST(:i AS jsonb), :h, CAST(:res AS jsonb), :s, :np, :nm, :nu, :u) RETURNING id"""),
        {"b": ctx.business_id, "r": body.run_id, "p": body.product_id, "v": CALC_VERSION, "d": inputs["as_of"], "i": canonical(inputs), "h": sha256(inputs),
         "res": canonical(results), "s": results["state"], "np": n["present"], "nm": n["missing"], "nu": n["uncertain"], "u": ctx.user.id}).scalar_one()
    record_event(db, action="readiness.run", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="readiness_check", target_id=cid,
                 details={"run_id": str(body.run_id), "product_id": body.product_id, "state": results["state"], **{f"{k}_count": v for k, v in n.items()},
                          "inputs_sha256": sha256(inputs)[:16]})
    db.commit()
    return _row(db.execute(text(_SELECT + " AND c.id = :i"), {"b": ctx.business_id, "i": cid}).mappings().one())


@router.get("")
def list_checks(run_id: UUID | None = None, ctx: BusinessContext = Depends(require("readiness.read")), db: Session = Depends(get_session)) -> list[dict]:
    where = " AND c.run_id = :r" if run_id else ""
    rows = db.execute(text(_SELECT + where + " ORDER BY c.created_at DESC LIMIT 50"), {"b": ctx.business_id, "r": run_id}).mappings().all()
    return [{k: v for k, v in _row(r).items() if k != "results"} | {"product_name": r["results"]["product"]["name"], "counts": r["results"]["counts"]} for r in rows]


def _load(db: Session, ctx: BusinessContext, check_id: UUID):
    r = db.execute(text(_SELECT + " AND c.id = :i"), {"b": ctx.business_id, "i": check_id}).mappings().first()
    if r is None:  # includes a check of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Readiness check not found")
    return r


@router.get("/{check_id}")
def get_check(check_id: UUID, ctx: BusinessContext = Depends(require("readiness.read")), db: Session = Depends(get_session)) -> dict:
    return _row(_load(db, ctx, check_id))


@router.get("/{check_id}/verify")
def verify(check_id: UUID, ctx: BusinessContext = Depends(require("readiness.read")), db: Session = Depends(get_session)) -> dict:
    r = _load(db, ctx, check_id)
    return {"check_id": str(r["id"]), "inputs_hash_matches": sha256(r["inputs"]) == r["inputs_sha256"],
            "results_reproduce": canonical(assess(r["inputs"])) == canonical(r["results"])}


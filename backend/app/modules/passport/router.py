"""Funding Passport: owner-selected, immutable packet previews for sandbox recipients only."""
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, freeze_inputs, load_snapshot, sha256
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/funding-passport", tags=["funding passport"])
RECIPIENTS = {"sandbox_lender_a": "Sandbox Lender A", "sandbox_lender_b": "Sandbox Lender B"}
FIELDS = {
    "business_name": "Business name",
    "jurisdiction": "Business jurisdiction",
    "base_currency": "Base currency",
    "cash_summary": "Latest saved cash outlook summary",
    "customer_dues": "Open customer dues by currency",
    "supplier_bills": "Open supplier bills by currency",
}


class PacketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: Literal["sandbox_lender_a", "sandbox_lender_b"]
    selected_fields: list[Literal["business_name", "jurisdiction", "base_currency", "cash_summary", "customer_dues", "supplier_bills"]] = Field(min_length=1, max_length=6)
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)
    expires_in_days: StrictInt = Field(default=7, ge=1, le=30)


def _today(db: Session, b: UUID) -> date:
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": b}).scalar_one()
    return business_today(tz)


def _docs(db: Session, b: UUID) -> list[dict]:
    rows = db.execute(text("""SELECT id, kind, reference, issued_on, expires_on FROM business_document
                            WHERE business_id = :b AND voided_at IS NULL ORDER BY kind, created_at DESC"""), {"b": b}).mappings().all()
    return [{"id": str(r["id"]), "kind": r["kind"], "where_kept": r["reference"],
             "issued_on": r["issued_on"].isoformat() if r["issued_on"] else None,
             "expires_on": r["expires_on"].isoformat() if r["expires_on"] else None} for r in rows]


def _source_data(db: Session, b: UUID, today: date) -> dict:
    profile = db.execute(text("SELECT name, jurisdiction, base_currency FROM business WHERE id = :b"), {"b": b}).mappings().one()
    snap = load_snapshot(db, b)
    cash = None
    cash_current = False
    if snap:
        current_hash = sha256(freeze_inputs(db, b, today))
        cash_current = snap["as_of"] == today.isoformat() and snap["inputs_sha256"] == current_hash and sha256(snap["inputs"]) == snap["inputs_sha256"]
        currencies = []
        for c in snap["results"].get("currencies", []):
            currencies.append({"currency": c["currency"], "reserve_minor": c.get("reserve_minor"),
                               "lowest_expected": c.get("lowest", {}).get("expected"),
                               "lowest_cautious": c.get("lowest", {}).get("cautious")})
        cash = {"as_of": snap["as_of"], "calculation": snap["calc_version"], "current": cash_current, "currencies": currencies,
                "source_note": "Values copied from the latest saved Cash Outlook; recalculate if it is stale."}
    obligations = list_obligations(db, b, today)
    sums: dict[tuple[str, str], int] = {}
    counts: dict[tuple[str, str], int] = {}
    disputed: dict[tuple[str, str], int] = {}
    for o in obligations:
        if o["status"] not in ("open", "partially_settled") or o["outstanding_minor"] <= 0:
            continue
        key = (o["direction"], o["currency"])
        if o["disputed"]:
            disputed[key] = disputed.get(key, 0) + o["outstanding_minor"]
        else:
            sums[key] = sums.get(key, 0) + o["outstanding_minor"]
            counts[key] = counts.get(key, 0) + 1
    def aggregate(direction: str) -> dict:
        kinds = {c for d, c in set(sums) | set(disputed) if d == direction}
        return {"as_of": today.isoformat(), "items": [{"currency": c, "open_count": counts.get((direction, c), 0),
                 "open_amount_minor": sums.get((direction, c), 0), "disputed_amount_minor": disputed.get((direction, c), 0)} for c in sorted(kinds)],
                "source": "Open obligations; disputed balances shown separately; names and invoice numbers omitted."}
    return {"business_name": {"value": profile["name"], "source": "Owner-maintained business profile"},
            "jurisdiction": {"value": profile["jurisdiction"], "source": "Owner-maintained business profile"},
            "base_currency": {"value": profile["base_currency"], "source": "Owner-maintained business profile"},
            "cash_summary": cash, "customer_dues": aggregate("receivable"), "supplier_bills": aggregate("payable")}


@router.get("")
def passport_options(ctx: BusinessContext = Depends(require("passport.read")), db: Session = Depends(get_session)) -> dict:
    return {"version": "passport-v1", "fields": [{"id": k, "label": v} for k, v in FIELDS.items()], "documents": _docs(db, ctx.business_id),
            "recipients": [{"id": k, "label": v} for k, v in RECIPIENTS.items()], "notice": "Preview only. Nothing is shared until the owner approves and runs it in the sandbox."}


@router.get("/packets")
def packets(ctx: BusinessContext = Depends(require("passport.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(text("SELECT id, packet_version, recipient, selected_fields, packet, packet_sha256, created_at, expires_at FROM funding_passport_packet WHERE business_id = :b ORDER BY created_at DESC LIMIT 20"), {"b": ctx.business_id}).mappings().all()
    return [{"id": str(r["id"]), "version": r["packet_version"], "recipient": r["recipient"], "selected_fields": r["selected_fields"],
             "packet": r["packet"], "packet_sha256": r["packet_sha256"], "created_at": r["created_at"].isoformat(),
             "expires_at": r["expires_at"].isoformat()} for r in rows]


@router.post("/packets", status_code=status.HTTP_201_CREATED)
def create_packet(body: PacketIn, ctx: BusinessContext = Depends(require("passport.write")), db: Session = Depends(get_session)) -> dict:
    if len(set(body.selected_fields)) != len(body.selected_fields):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose each packet section only once.")
    today = _today(db, ctx.business_id)
    data = _source_data(db, ctx.business_id, today)
    packet = {key: data[key] for key in body.selected_fields}
    all_docs = {d["id"]: d for d in _docs(db, ctx.business_id)}
    if any(str(i) not in all_docs for i in body.document_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A selected evidence entry is missing or has been voided.")
    if body.document_ids:
        # Disclose evidence type and dates only; the internal register location and row ID stay private.
        packet["selected_evidence"] = [{"kind": all_docs[str(i)]["kind"], "issued_on": all_docs[str(i)]["issued_on"],
                                        "expires_on": all_docs[str(i)]["expires_on"],
                                        "status": "Recorded in the business evidence register; original retained by the business"}
                                       for i in body.document_ids]
    if "cash_summary" in body.selected_fields and data["cash_summary"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Calculate Cash Outlook before including a cash summary.")
    if "cash_summary" in body.selected_fields and not data["cash_summary"]["current"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The saved Cash Outlook is stale or its inputs changed; recalculate it before sharing.")
    created = db.execute(text("SELECT now()" )).scalar_one()
    expires = created + timedelta(days=body.expires_in_days)
    digest = sha256(packet)
    packet_id = db.execute(text("""INSERT INTO funding_passport_packet (business_id, packet_version, recipient, selected_fields, source_document_ids, packet,
                     packet_sha256, created_by, created_at, expires_at)
                     VALUES (:b, 'passport-v1', :r, CAST(:f AS jsonb), :docs, CAST(:p AS jsonb), :h, :u, :c, :e) RETURNING id"""),
                     {"b": ctx.business_id, "r": body.recipient, "f": canonical(body.selected_fields + (["selected_evidence"] if body.document_ids else [])),
                      "docs": [str(i) for i in body.document_ids], "p": canonical(packet), "h": digest, "u": ctx.user.id, "c": created, "e": expires}).scalar_one()
    record_event(db, action="passport.packet_create", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role),
                 business_id=ctx.business_id, target_type="funding_passport_packet", target_id=packet_id,
                 details={"version": "passport-v1", "recipient": body.recipient, "fields": body.selected_fields,
                          "evidence_count": len(body.document_ids), "expires_at": expires.isoformat(), "packet_sha256": digest[:16]})
    db.commit()
    return {"id": str(packet_id), "version": "passport-v1", "recipient": body.recipient, "recipient_label": RECIPIENTS[body.recipient],
            "selected_fields": body.selected_fields + (["selected_evidence"] if body.document_ids else []), "packet": packet,
            "packet_sha256": digest, "created_at": created.isoformat(), "expires_at": expires.isoformat(),
            "notice": "Exact preview saved. Sharing still requires a separate owner-approved sandbox proposal."}

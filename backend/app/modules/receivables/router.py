"""Receivables Guardian API: invoice ageing, who to contact, verified customer contacts, and internal reminder drafts.

There is no send route. A draft reaches a customer only as an `invoice_reminder` proposal in the approval workflow
(POST /approvals with the draft's ID), approved by an owner, released, and run by the sandbox executor to a verified
contact. Nothing here changes an invoice or a payment.
"""

import re
import unicodedata
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.db import get_session
from app.modules.approvals.router import _text
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, sha256
from app.modules.receivables import service
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/receivables", tags=["receivables guardian"])
EMAIL = re.compile(r"^[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}$")
MOBILE = re.compile(r"^01[3-9]\d{8}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactIn(_Strict):
    party_id: UUID
    channel: Literal["email", "sms"]
    address: StrictStr
    source: Literal["customer_form", "signed_contract", "invoice_document", "customer_email", "phone_call", "in_person"]

    @field_validator("address")
    @classmethod
    def strip(cls, v: str) -> str:
        v = unicodedata.normalize("NFC", v).strip()
        if not v or len(v) > 254:
            raise ValueError("enter an email address or mobile number")
        return v


class ContactReviewIn(_Strict):
    decision: Literal["verify", "reject", "retire"]
    method: Literal["called_known_number", "confirmed_in_person", "signed_customer_form"] | None = None
    checked_on: date | None = None
    reason: StrictStr
    validate_reason = field_validator("reason")(_text(300, min_len=3, no_links=True))


class DraftIn(_Strict):
    obligation_id: UUID
    contact_id: UUID
    note: StrictStr | None = None
    validate_note = field_validator("note")(_text(200, min_len=3, no_links=True))


class DiscardIn(_Strict):
    reason: StrictStr
    validate_reason = field_validator("reason")(_text(300, min_len=3, no_links=True))


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _biz(db: Session, b: UUID) -> tuple[str, str]:
    r = db.execute(text("SELECT name, timezone FROM business WHERE id = :b"), {"b": b}).mappings().one()
    return r["name"], r["timezone"]


def _field(field: str, message: str, code: int = status.HTTP_422_UNPROCESSABLE_ENTITY):
    """An error placed under one form field, in the same shape as a validation error."""
    return HTTPException(code, [{"loc": ["body", field], "msg": message, "type": "value_error"}])


def _normalise(channel: str, address: str) -> str:
    if channel == "email":
        a = address.lower()
        if not EMAIL.match(a) or ".." in a:
            raise _field("address", "Enter a valid email address, such as accounts@customer.example.")
        return a
    digits = re.sub(r"[\s-]", "", address)
    digits = re.sub(r"^(\+?88)", "", digits)
    if not MOBILE.match(digits):
        raise _field("address", "Enter a Bangladesh mobile number: 11 digits starting with 013 to 019.")
    return digits


# --- reading -------------------------------------------------------------------------------------------------


@router.get("")
def guardian(ctx: BusinessContext = Depends(require("receivables.read")), db: Session = Depends(get_session)) -> dict:
    _, tz = _biz(db, ctx.business_id)
    return service.overview(db, ctx.business_id, business_today(tz), tz) | {
        "verify_methods": service.VERIFY_METHODS, "contact_sources": service.CONTACT_SOURCES}


@router.get("/contacts")
def list_contacts(ctx: BusinessContext = Depends(require("receivables.read")), db: Session = Depends(get_session)) -> dict:
    customers = db.execute(text(
        """SELECT DISTINCT p.id, p.name FROM party p
           WHERE p.business_id = :b AND (EXISTS (SELECT 1 FROM obligation o WHERE o.business_id = p.business_id AND o.party_id = p.id AND o.direction = 'receivable')
                                         OR EXISTS (SELECT 1 FROM party_contact c WHERE c.business_id = p.business_id AND c.party_id = p.id))
           ORDER BY p.name"""), {"b": ctx.business_id}).mappings().all()
    return {"customers": [{"party_id": str(c["id"]), "name": c["name"]} for c in customers],
            "contacts": service.contacts(db, ctx.business_id, ctx.user.id),
            "sources": service.CONTACT_SOURCES, "methods": service.VERIFY_METHODS}


@router.get("/drafts")
def list_drafts(ctx: BusinessContext = Depends(require("receivables.read")), db: Session = Depends(get_session)) -> dict:
    return {"items": service.drafts(db, ctx.business_id, utc_now())}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: UUID, ctx: BusinessContext = Depends(require("receivables.read")), db: Session = Depends(get_session)) -> dict:
    found = service.drafts(db, ctx.business_id, utc_now(), draft_id)
    if not found:  # includes another business's draft
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    return found[0]


# --- contacts ------------------------------------------------------------------------------------------------


@router.post("/contacts", status_code=status.HTTP_201_CREATED)
def add_contact(body: ContactIn, ctx: BusinessContext = Depends(require("contacts.write")), db: Session = Depends(get_session)) -> dict:
    party = db.execute(text("SELECT id, name FROM party WHERE business_id = :b AND id = :p"), {"b": ctx.business_id, "p": body.party_id}).mappings().first()
    if party is None:  # includes another business's customer
        raise _field("party_id", "That customer was not found.")
    address = _normalise(body.channel, body.address)
    other = db.execute(text("SELECT c.party_id, p.name FROM party_contact c JOIN party p ON p.id = c.party_id WHERE c.business_id = :b AND c.channel = :ch AND c.address = :a"),
                       {"b": ctx.business_id, "ch": body.channel, "a": address}).mappings().first()
    if other is not None:
        same = other["party_id"] == party["id"]
        record_event(db, action="receivables.contact_add", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id, target_type="party",
                     target_id=party["id"], details={"channel": body.channel, "address": service.mask(body.channel, address), "reason": "duplicate" if same else "other customer"})
        db.commit()
        raise _field("address", f"This {'address' if body.channel == 'email' else 'number'} is already on file for "
                                f"{'this customer' if same else other['name']}.", status.HTTP_409_CONFLICT)
    try:
        cid = db.execute(text("""INSERT INTO party_contact (business_id, party_id, channel, address, source, added_by, added_role)
                                 VALUES (:b, :p, :ch, :a, :s, :u, :r) RETURNING id"""),
                         {"b": ctx.business_id, "p": party["id"], "ch": body.channel, "a": address, "s": body.source, "u": ctx.user.id, "r": ctx.role}).scalar_one()
    except IntegrityError as exc:  # the same address added at the same moment
        db.rollback()
        raise _field("address", "This contact is already on file.", status.HTTP_409_CONFLICT) from exc
    record_event(db, action="receivables.contact_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="party_contact",
                 target_id=cid, details={"customer": party["name"], "channel": body.channel, "address": service.mask(body.channel, address), "source": body.source})
    db.commit()
    return service.contacts(db, ctx.business_id, ctx.user.id, cid)[0]


@router.post("/contacts/{contact_id}/reviews")
def review_contact(contact_id: UUID, body: ContactReviewIn, ctx: BusinessContext = Depends(require("contacts.verify")),
                   db: Session = Depends(get_session)) -> dict:
    db.execute(text("SELECT id FROM party_contact WHERE business_id = :b AND id = :c FOR UPDATE"), {"b": ctx.business_id, "c": contact_id})
    c = service.contact_row(db, ctx.business_id, contact_id)
    if c is None:  # includes another business's contact
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    _, tz = _biz(db, ctx.business_id)
    decision = {"verify": "verified", "reject": "rejected", "retire": "retired"}[body.decision]

    def refuse(message: str, field: str | None = None):
        record_event(db, action="receivables.contact_review", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id,
                     target_type="party_contact", target_id=c["id"], details={"decision": decision, "address": service.mask(c["channel"], c["address"]), "reason": message})
        db.commit()
        raise _field(field, message, status.HTTP_409_CONFLICT) if field else HTTPException(status.HTTP_409_CONFLICT, message)

    if c["state"] in ("rejected", "retired"):
        refuse(f"This contact was already {c['state']}; add a new contact instead.")
    if decision in ("verified", "rejected") and c["state"] == "verified":
        refuse("This contact is already verified. Retire it if it is no longer correct.")
    if decision in ("verified", "rejected") and c["added_by"] == ctx.user.id:
        refuse("You added this contact, so someone else must verify or reject it.")
    if decision == "verified":
        added_on = service.local_date(c["created_at"], tz)
        if body.method is None:
            raise _field("method", "Choose how the contact was verified.")
        if body.checked_on is None:
            raise _field("checked_on", "Enter the date it was verified.")
        if not added_on <= body.checked_on <= business_today(tz):
            raise _field("checked_on", f"The date must be between {service.dmy(added_on)} (when it was added) and today.")
    elif body.method is not None or body.checked_on is not None:
        raise _field("method" if body.method is not None else "checked_on", "A method and date are recorded only when verifying.")
    db.execute(text("""INSERT INTO party_contact_review (business_id, contact_id, decision, method, checked_on, reason, actor_id, actor_role)
                       VALUES (:b, :c, :d, :m, :on, :r, :u, :role)"""),
               {"b": ctx.business_id, "c": c["id"], "d": decision, "m": body.method if decision == "verified" else None,
                "on": body.checked_on if decision == "verified" else None, "r": body.reason, "u": ctx.user.id, "role": ctx.role})
    record_event(db, action="receivables.contact_review", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="party_contact",
                 target_id=c["id"], details={"customer": c["party_name"], "decision": decision, "address": service.mask(c["channel"], c["address"]),
                                             "method": body.method if decision == "verified" else None, "reason": body.reason})
    db.commit()
    return service.contacts(db, ctx.business_id, ctx.user.id, c["id"])[0]


# --- drafts --------------------------------------------------------------------------------------------------


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def create_draft(body: DraftIn, ctx: BusinessContext = Depends(require("receivables.draft")), db: Session = Depends(get_session)) -> dict:
    name, tz = _biz(db, ctx.business_id)
    today = business_today(tz)
    # Lock the invoice: two drafts for it are checked one after the other.
    db.execute(text("SELECT id FROM obligation WHERE business_id = :b AND id = :o FOR UPDATE"), {"b": ctx.business_id, "o": body.obligation_id})
    found = list_obligations(db, ctx.business_id, today, obligation_id=body.obligation_id)
    if not found or found[0]["direction"] != "receivable":  # includes a bill and another business's invoice
        raise _field("obligation_id", "That invoice was not found.")
    o = found[0]
    contact = service.contact_row(db, ctx.business_id, body.contact_id)
    if contact is None:
        raise _field("contact_id", "That contact was not found.")
    now = utc_now()
    props = service.reminder_proposals(db, ctx.business_id, now, o["id"])
    active = [d for d in (service.draft_out(r, props) for r in service.draft_rows(db, ctx.business_id, obligation_id=o["id"])) if d["state"] in service.ACTIVE]

    def refuse(message: str, field: str | None, code: int):
        record_event(db, action="receivables.draft_create", outcome="conflict" if code == status.HTTP_409_CONFLICT else "failed", actor=_actor(ctx),
                     business_id=ctx.business_id, target_type="obligation", target_id=o["id"], details={"document_number": o["document_number"], "reason": message})
        db.commit()
        raise _field(field, message, code) if field else HTTPException(code, message)

    if active:
        refuse(f"{o['document_number']} already has a reminder draft ({active[0]['state'].replace('_', ' ')}); use or discard it first.", None, status.HTTP_409_CONFLICT)
    found_problems = service.problems(db, ctx.business_id, today, tz, o, contact, props)
    if found_problems:
        msg = found_problems[0]
        field = "contact_id" if "contact" in msg.lower() else "obligation_id" if any(w in msg for w in ("disputed", "not overdue", "nothing is outstanding")) else None
        refuse(msg, field, status.HTTP_409_CONFLICT if field is None else status.HTTP_422_UNPROCESSABLE_ENTITY)
    facts = service.facts_of(o)
    message = service.message_for(facts, name, body.note)
    did = db.execute(text(
        """INSERT INTO reminder_draft (business_id, obligation_id, party_id, contact_id, channel, rules_version, facts, facts_sha256, note, message,
                                       created_by, created_role)
           VALUES (:b, :o, :p, :c, :ch, :v, CAST(:f AS jsonb), :h, :n, :m, :u, :r) RETURNING id"""),
        {"b": ctx.business_id, "o": o["id"], "p": o["party"]["id"], "c": contact["id"], "ch": contact["channel"], "v": service.RULES_VERSION,
         "f": canonical(facts), "h": sha256(facts), "n": body.note, "m": message, "u": ctx.user.id, "r": ctx.role}).scalar_one()
    record_event(db, action="receivables.draft_create", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="reminder_draft",
                 target_id=did, details={"document_number": o["document_number"], "customer": o["party"]["name"], "outstanding_minor": o["outstanding_minor"],
                                         "currency": o["currency"], "channel": contact["channel"], "recipient": service.mask(contact["channel"], contact["address"]),
                                         "facts_sha256": sha256(facts)[:16]})
    db.commit()
    return get_draft(did, ctx, db)


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: UUID, body: DiscardIn, ctx: BusinessContext = Depends(require("receivables.draft")), db: Session = Depends(get_session)) -> dict:
    rows = service.draft_rows(db, ctx.business_id, draft_id, lock=True)
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    d = service.draft_out(rows[0], service.reminder_proposals(db, ctx.business_id, utc_now(), str(rows[0]["obligation_id"])))
    if d["state"] not in ("draft", "rejected"):
        record_event(db, action="receivables.draft_discard", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id, target_type="reminder_draft",
                     target_id=draft_id, details={"state": d["state"]})
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"A draft that is {d['state'].replace('_', ' ')} cannot be discarded"
                            + ("; withdraw or reject its proposal first." if d["state"] in ("awaiting_approval", "approved") else "."))
    db.execute(text("UPDATE reminder_draft SET discarded_at = clock_timestamp(), discarded_by = :u, discard_reason = :r WHERE id = :d"),
               {"u": ctx.user.id, "r": body.reason, "d": draft_id})
    record_event(db, action="receivables.draft_discard", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="reminder_draft",
                 target_id=draft_id, details={"document_number": d["document_number"], "reason": body.reason})
    db.commit()
    return get_draft(draft_id, ctx, db)

"""Receivables Guardian API: age receivables, suggest whom to contact, keep verified contacts, draft reminders, and hand a draft
to the existing approval workflow. Sending happens only through the existing sandbox executor after approval and release.

Nothing here marks an invoice paid, changes an invoice, or sends anything. A reminder outcome is a record next to the invoice;
only a recorded payment (the obligations module) reduces what is outstanding.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.config import get_settings
from app.db import get_session
from app.modules.approvals import router as approvals
from app.modules.approvals.policy import iso, state as proposal_state
from app.modules.audit.service import Actor, record_event
from app.modules.executor.service import summaries as execution_summaries
from app.modules.guardian import engine
from app.modules.identity.deps import BusinessContext, require
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, sha256
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/guardian", tags=["receivables guardian"])
METHODS = ("called_known_contact", "met_in_person", "written_confirmation")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactIn(_Strict):
    channel: Literal["email", "sms"]
    address: StrictStr = Field(max_length=120)


class ReviewIn(_Strict):
    decision: Literal["verify", "reject"]
    method: Literal["called_known_contact", "met_in_person", "written_confirmation"] | None = None
    reference: StrictStr | None = None
    reason: StrictStr | None = None
    validate_ref = field_validator("reference", "reason")(approvals._text(120, min_len=3, no_links=True))


class DraftIn(_Strict):
    obligation_id: UUID
    channel: Literal["email", "sms"]
    note: StrictStr | None = None
    validate_note = field_validator("note")(approvals._text(150, min_len=3, no_links=True))


class ProposeIn(_Strict):
    expires_in_hours: StrictInt | None = Field(default=None, ge=1, le=72)


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _today(db: Session, b: UUID) -> date:
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": b}).scalar_one())


# --- contacts -------------------------------------------------------------------------------------------------

_CSEL = """SELECT c.*, u.display_name AS recorded_by_name, r.decision, r.method, r.reference, r.reason, r.created_at AS reviewed_at, v.display_name AS reviewer_name
           FROM customer_contact c JOIN app_user u ON u.id = c.recorded_by
           LEFT JOIN customer_contact_review r ON r.contact_id = c.id LEFT JOIN app_user v ON v.id = r.reviewer_id
           WHERE c.business_id = :b"""


def _contact(r, current: bool) -> dict:
    return {"id": str(r["id"]), "party_id": str(r["party_id"]), "channel": r["channel"], "address_masked": r["address_masked"],
            "status": r["decision"] or "unverified", "current": current, "recorded_by": r["recorded_by_name"], "created_at": iso(r["created_at"]),
            "reviewed_by": r["reviewer_name"], "reviewed_at": iso(r["reviewed_at"]) if r["reviewed_at"] else None,
            "method": r["method"], "reference": r["reference"], "reject_reason": r["reason"]}


def contacts_for(db: Session, b: UUID, party_id: UUID | None = None) -> list[dict]:
    rows = db.execute(text(_CSEL + (" AND c.party_id = :p" if party_id else "") + " ORDER BY c.created_at DESC, c.id DESC"), {"b": b, "p": party_id}).mappings().all()
    seen, out = set(), []
    for r in rows:   # newest first: the first contact per customer and channel is the current one
        key = (r["party_id"], r["channel"])
        out.append(_contact(r, key not in seen))
        seen.add(key)
    return out


def _party(db: Session, b: UUID, party_id: UUID) -> dict:
    r = db.execute(text("SELECT p.id, p.name FROM party p WHERE p.business_id = :b AND p.id = :p AND EXISTS "
                        "(SELECT 1 FROM obligation o WHERE o.business_id = p.business_id AND o.party_id = p.id AND o.direction = 'receivable')"),
                   {"b": b, "p": party_id}).mappings().first()
    if r is None:  # includes another business's customer
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    return {"id": str(r["id"]), "name": r["name"]}


@router.get("/customers/{party_id}/contacts")
def customer_contacts(party_id: UUID, ctx: BusinessContext = Depends(require("guardian.read")), db: Session = Depends(get_session)) -> list[dict]:
    _party(db, ctx.business_id, party_id)
    return contacts_for(db, ctx.business_id, party_id)


@router.post("/customers/{party_id}/contacts", status_code=status.HTTP_201_CREATED)
def add_contact(party_id: UUID, body: ContactIn, ctx: BusinessContext = Depends(require("contact.write")), db: Session = Depends(get_session)) -> dict:
    party = _party(db, ctx.business_id, party_id)
    try:
        masked, digest = engine.normalize_address(body.channel, body.address, str(ctx.business_id), get_settings().destination_hash_key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    dup = db.execute(text("SELECT 1 FROM customer_contact WHERE business_id = :b AND party_id = :p AND channel = :c AND address_sha256 = :h"),
                     {"b": ctx.business_id, "p": party_id, "c": body.channel, "h": digest}).first()
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, "That contact is already recorded for this customer.")
    cid = db.execute(text(
        """INSERT INTO customer_contact (business_id, party_id, channel, address_masked, address_sha256, recorded_by)
           VALUES (:b, :p, :c, :m, :h, :u) RETURNING id"""),
        {"b": ctx.business_id, "p": party_id, "c": body.channel, "m": masked, "h": digest, "u": ctx.user.id}).scalar_one()
    record_event(db, action="guardian.contact_add", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="customer_contact", target_id=cid,
                 details={"customer": party["name"], "channel": body.channel, "address": masked})
    db.commit()
    return next(c for c in contacts_for(db, ctx.business_id, party_id) if c["id"] == str(cid))


@router.post("/contacts/{contact_id}/review")
def review_contact(contact_id: UUID, body: ReviewIn, ctx: BusinessContext = Depends(require("contact.verify")), db: Session = Depends(get_session)) -> dict:
    row = db.execute(text(_CSEL + " AND c.id = :i FOR UPDATE OF c"), {"b": ctx.business_id, "i": contact_id}).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contact not found")
    if row["decision"]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This contact was already {row['decision']}. Record a new contact to change it.")
    if body.decision == "verify" and not (body.method and body.reference):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say how the contact was verified and where that is documented.")
    if body.decision == "reject" and not body.reason:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give the reason the contact is rejected.")
    db.execute(text(
        """INSERT INTO customer_contact_review (business_id, contact_id, decision, method, reference, reason, reviewer_id)
           VALUES (:b, :c, :d, :m, :r, :why, :u)"""),
        {"b": ctx.business_id, "c": contact_id, "d": "verified" if body.decision == "verify" else "rejected",
         "m": body.method if body.decision == "verify" else None, "r": body.reference if body.decision == "verify" else None,
         "why": body.reason if body.decision == "reject" else None, "u": ctx.user.id})
    record_event(db, action="guardian.contact_review", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="customer_contact", target_id=contact_id,
                 details={"decision": body.decision, "method": body.method, "address": row["address_masked"], "channel": row["channel"]})
    db.commit()
    return next(c for c in contacts_for(db, ctx.business_id, row["party_id"]) if c["id"] == str(contact_id))


# --- invoices, history, overview ------------------------------------------------------------------------------


def _history(db: Session, b: UUID, now: datetime) -> dict[str, list[dict]]:
    """Per invoice: every draft, its approval proposal and state, and the sandbox outcome. A recorded outcome never touches the invoice."""
    drafts = db.execute(text(
        """SELECT d.id, d.obligation_id, d.channel, d.message, d.facts, d.created_at, u.display_name AS created_by_name, l.proposal_id
           FROM reminder_draft d JOIN app_user u ON u.id = d.created_by LEFT JOIN reminder_draft_link l ON l.draft_id = d.id
           WHERE d.business_id = :b ORDER BY d.created_at DESC"""), {"b": b}).mappings().all()
    ids = [d["proposal_id"] for d in drafts if d["proposal_id"]]
    props = {r["id"]: r for r in db.execute(text("SELECT * FROM action_proposal WHERE business_id = :b AND id = ANY(:i)"), {"b": b, "i": ids}).mappings()} if ids else {}
    decisions = approvals._decisions(db, ids)
    runs = execution_summaries(db, b, ids)
    out: dict[str, list[dict]] = {}
    for d in drafts:
        pid = d["proposal_id"]
        st = proposal_state(props[pid], decisions[pid], now) if pid else "draft"
        run = runs.get(pid) if pid else None
        out.setdefault(str(d["obligation_id"]), []).append({
            "draft_id": str(d["id"]), "channel": d["channel"], "created_at": iso(d["created_at"]), "created_by": d["created_by_name"],
            "proposal_id": str(pid) if pid else None, "state": st, "stage": d["facts"]["stage"],
            "execution": run, "sent_in_sandbox": bool(run and run["outcome"] == "succeeded"),
            "sent_at": run["completed_at"] if run and run["outcome"] == "succeeded" else None})
    return out


def _invoice_rows(db: Session, b: UUID, today: date, now: datetime) -> tuple[list[dict], dict]:
    invs = [o for o in list_obligations(db, b, today, direction="receivable") if o["status"] in ("open", "partially_settled")]
    history = _history(db, b, now)
    for i in invs:
        hist = history.get(i["id"], [])
        sent = [h["sent_at"] for h in hist if h["sent_in_sandbox"] and h["sent_at"]]
        last = max(datetime.fromisoformat(s) for s in sent).date() if sent else None
        open_prop = any(h["state"] in ("pending", "approved", "executable") and not (h["execution"] and h["execution"]["outcome"] in ("succeeded", "rejected")) for h in hist)
        i["bucket"] = engine.bucket(i["days_overdue"])
        i["last_sent"] = last.isoformat() if last else None
        i["blockers"] = engine.blockers(i, open_proposal=open_prop, last_sent=last, today=today)
        i["history"] = hist
    return invs, history


def _live_contact(db: Session, b: UUID, party_id: str, channel: str) -> dict | None:
    cs = [c for c in contacts_for(db, b, UUID(party_id)) if c["channel"] == channel and c["current"]]
    return cs[0] if cs else None


@router.get("")
def overview(ctx: BusinessContext = Depends(require("guardian.read")), db: Session = Depends(get_session)) -> dict:
    today, now = _today(db, ctx.business_id), utc_now()
    invs, _ = _invoice_rows(db, ctx.business_id, today, now)
    contacts = contacts_for(db, ctx.business_id)
    by_party: dict[str, dict] = {}
    for i in invs:
        by_party.setdefault(i["party"]["id"], {"party": i["party"], "invoices": []})["invoices"].append(i)
    customers = []
    for pid, c in by_party.items():
        cur = {x["channel"]: x for x in contacts if x["party_id"] == pid and x["current"]}
        for i in c["invoices"]:
            i["eligible"] = not i["blockers"]
        customers.append({"party": c["party"], "invoices": c["invoices"], "contacts": {ch: cur.get(ch) for ch in ("email", "sms")},
                          "has_verified_contact": any(x and x["status"] == "verified" for x in cur.values())})
    suggestions = engine.suggest(customers)
    ready = {s["party_id"]: next(c for c in customers if c["party"]["id"] == s["party_id"]) for s in suggestions}
    for s in suggestions:
        s["has_verified_contact"] = ready[s["party_id"]]["has_verified_contact"]
        s["next_step"] = "Draft a reminder" if s["has_verified_contact"] else "Record and verify a contact first"
    overdue = [i for i in invs if i["days_overdue"] > 0 or i["disputed"]]
    return {"rules_version": engine.RULES_VERSION, "as_of": today.isoformat(), "cooldown_days": engine.COOLDOWN_DAYS, "aging": engine.age(invs),
            "suggestions": suggestions, "customers": sorted(customers, key=lambda c: c["party"]["name"].lower()),
            "overdue_count": len([i for i in overdue if not i["disputed"]]), "disputed_count": len([i for i in invs if i["disputed"]]),
            "notice": "Drafts stay internal. A reminder is sent only after owner approval, through the sandbox executor, to a verified contact. "
                      "Sending a reminder never marks an invoice paid."}


# --- drafts ---------------------------------------------------------------------------------------------------


def _draft_out(db: Session, ctx: BusinessContext, r, now: datetime) -> dict:
    hist = next((h for h in _history(db, ctx.business_id, now).get(str(r["obligation_id"]), []) if h["draft_id"] == str(r["id"])), None)
    return {"id": str(r["id"]), "obligation_id": str(r["obligation_id"]), "channel": r["channel"], "message": r["message"], "facts": r["facts"],
            "facts_sha256": r["facts_sha256"], "created_at": iso(r["created_at"]), "created_by": r["created_by_name"],
            "contact": next((c for c in contacts_for(db, ctx.business_id) if c["id"] == str(r["contact_id"])), None),
            "state": hist["state"] if hist else "draft", "proposal_id": hist["proposal_id"] if hist else None,
            "execution": hist["execution"] if hist else None, "sent_in_sandbox": bool(hist and hist["sent_in_sandbox"]),
            "internal_only": not (hist and hist["proposal_id"]),
            "invoice_still_unpaid_note": "A reminder outcome is a separate record. The invoice stays outstanding until a payment is recorded against it."}


_DSEL = """SELECT d.*, u.display_name AS created_by_name FROM reminder_draft d JOIN app_user u ON u.id = d.created_by WHERE d.business_id = :b"""


def _invoice(db: Session, b: UUID, today: date, obligation_id: UUID) -> dict:
    db.execute(text("SELECT id FROM obligation WHERE business_id = :b AND id = :o FOR UPDATE"), {"b": b, "o": obligation_id})
    found = list_obligations(db, b, today, obligation_id=obligation_id)
    if not found or found[0]["direction"] != "receivable":  # includes another business's invoice
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return found[0]


def _check_sendable(db: Session, ctx: BusinessContext, inv: dict, today: date, now: datetime) -> None:
    _, history = _invoice_rows(db, ctx.business_id, today, now)
    hist = history.get(inv["id"], [])
    sent = [h["sent_at"] for h in hist if h["sent_in_sandbox"] and h["sent_at"]]
    open_prop = any(h["state"] in ("pending", "approved", "executable") and not (h["execution"] and h["execution"]["outcome"] in ("succeeded", "rejected")) for h in hist)
    last = max(datetime.fromisoformat(s) for s in sent).date() if sent else None
    reasons = engine.blockers(inv, open_proposal=open_prop, last_sent=last, today=today)
    if reasons:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, " ".join(reasons))


@router.post("/drafts", status_code=status.HTTP_201_CREATED)
def create_draft(body: DraftIn, ctx: BusinessContext = Depends(require("guardian.draft")), db: Session = Depends(get_session)) -> dict:
    today, now = _today(db, ctx.business_id), utc_now()
    inv = _invoice(db, ctx.business_id, today, body.obligation_id)
    _check_sendable(db, ctx, inv, today, now)
    contact = _live_contact(db, ctx.business_id, inv["party"]["id"], body.channel)
    if contact is None or contact["status"] != "verified":
        why = "has no contact recorded" if contact is None else "has a contact that was rejected" if contact["status"] == "rejected" else "has a contact that is not verified yet"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{inv['party']['name']} {why} for {body.channel}. A reminder goes only to a verified contact.")
    facts = engine.facts_of(inv, contact["id"], body.channel, today)
    name = db.execute(text("SELECT name FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
    message = engine.draft_message(facts, name, body.note)
    did = db.execute(text(
        """INSERT INTO reminder_draft (business_id, obligation_id, contact_id, channel, message, facts, facts_sha256, created_by)
           VALUES (:b, :o, :c, :ch, :m, CAST(:f AS jsonb), :h, :u) RETURNING id"""),
        {"b": ctx.business_id, "o": body.obligation_id, "c": contact["id"], "ch": body.channel, "m": message, "f": canonical(facts), "h": sha256(facts), "u": ctx.user.id}).scalar_one()
    record_event(db, action="guardian.draft_create", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="reminder_draft", target_id=did,
                 details={"document_number": inv["document_number"], "channel": body.channel, "stage": facts["stage"], "facts_sha256": sha256(facts)[:16]})
    db.commit()
    return _draft_out(db, ctx, db.execute(text(_DSEL + " AND d.id = :i"), {"b": ctx.business_id, "i": did}).mappings().one(), now)


@router.get("/drafts")
def list_drafts(obligation_id: UUID | None = None, ctx: BusinessContext = Depends(require("guardian.read")), db: Session = Depends(get_session)) -> list[dict]:
    now = utc_now()
    rows = db.execute(text(_DSEL + (" AND d.obligation_id = :o" if obligation_id else "") + " ORDER BY d.created_at DESC LIMIT 100"), {"b": ctx.business_id, "o": obligation_id}).mappings().all()
    return [_draft_out(db, ctx, r, now) for r in rows]


def _load_draft(db: Session, ctx: BusinessContext, draft_id: UUID, lock: bool = False):
    r = db.execute(text(_DSEL + " AND d.id = :i" + (" FOR UPDATE OF d" if lock else "")), {"b": ctx.business_id, "i": draft_id}).mappings().first()
    if r is None:  # includes another business's draft
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found")
    return r


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: UUID, ctx: BusinessContext = Depends(require("guardian.read")), db: Session = Depends(get_session)) -> dict:
    return _draft_out(db, ctx, _load_draft(db, ctx, draft_id), utc_now())


@router.post("/drafts/{draft_id}/propose", status_code=status.HTTP_201_CREATED)
def propose_draft(draft_id: UUID, body: ProposeIn, ctx: BusinessContext = Depends(require("approvals.propose")), db: Session = Depends(get_session)) -> dict:
    """Send the draft to the existing approval workflow as an invoice-reminder proposal. Approval, release, and sending use the existing endpoints."""
    d = _load_draft(db, ctx, draft_id, lock=True)
    if db.execute(text("SELECT 1 FROM reminder_draft_link WHERE draft_id = :d"), {"d": draft_id}).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "This draft was already sent for approval. Create a new draft to change the message.")
    today, now = _today(db, ctx.business_id), utc_now()
    inv = _invoice(db, ctx.business_id, today, d["obligation_id"])
    f = d["facts"]
    live = engine.facts_of(inv, f["contact_id"], f["channel"], today)
    if any(live[k] != f[k] for k in ("document_number", "customer", "outstanding_minor", "currency", "due_date")):
        raise HTTPException(status.HTTP_409_CONFLICT, "The invoice changed after this draft was written (amount, due date, or customer). Create a new draft.")
    _check_sendable(db, ctx, inv, today, now)
    req = approvals.ReminderIn(kind="invoice_reminder", obligation_id=d["obligation_id"], channel=f["channel"], message=d["message"],
                               contact_id=UUID(f["contact_id"]), expires_in_hours=body.expires_in_hours)
    proposal = approvals.propose(req, ctx, db)   # validates the verified contact and the invoice again; commits the proposal
    db.execute(text("INSERT INTO reminder_draft_link (draft_id, business_id, proposal_id, linked_by) VALUES (:d, :b, :p, :u)"),
               {"d": draft_id, "b": ctx.business_id, "p": proposal["id"], "u": ctx.user.id})
    record_event(db, action="guardian.draft_propose", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="reminder_draft", target_id=draft_id,
                 details={"proposal_id": proposal["id"], "document_number": f["document_number"], "payload_sha256": proposal["payload_sha256"][:16]})
    db.commit()
    return {"draft": _draft_out(db, ctx, _load_draft(db, ctx, draft_id), now), "proposal": proposal}

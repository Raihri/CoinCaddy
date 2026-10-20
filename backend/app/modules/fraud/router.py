"""Transaction Fraud Monitor API: run the versioned rules, read flags with their compared records and bounded cash
outcomes, review a flag (confirm, clear, or defer with a reason), and record payee destinations.

A flag is suspected activity, not a fraud verdict. Nothing here changes a ledger transaction, an invoice or bill, a
payment, or a proposal, and nothing pays or contacts anyone: a hold only stops a proposed sandbox payment from being
approved or released (enforced by the approval API and by a database trigger).
"""

import re
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.db import get_session
from app.modules.approvals.router import _text
from app.modules.audit.service import Actor, record_event
from app.modules.fraud import rules
from app.modules.fraud.service import (UNRESOLVED, cash_outcomes, destination_hmac, flag_state, holds_for, masked, open_payment_proposals,
                                       reviews_for, run_scan)
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.service import canonical
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/fraud", tags=["fraud monitor"])

VERIFICATION_METHODS = {
    "called_known_contact": "Called the payee on a contact already on file before the change",
    "met_in_person": "Confirmed in person with a known contact",
}
CHANGE_SOURCES = {
    "onboarding": "Supplier set-up (first destination)",
    "supplier_email": "Email from the supplier",
    "supplier_letter": "Letter from the supplier",
    "phone_call_received": "Phone call received",
    "in_person": "Given in person",
    "invoice_document": "Printed on an invoice",
}
MAX_DEFER_DAYS = 30


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationIn(_Strict):
    method: Literal["called_known_contact", "met_in_person"]
    contact_source: StrictStr
    contact_predates_change: StrictBool
    checked_on: date
    validate_source = field_validator("contact_source")(_text(120, min_len=3, no_links=True))


class ReviewIn(_Strict):
    decision: Literal["confirm", "reject", "defer"]
    reason: StrictStr
    defer_until: date | None = None
    verification: VerificationIn | None = None
    validate_reason = field_validator("reason")(_text(300, min_len=3, no_links=True))


class DestinationIn(_Strict):
    party_id: UUID
    kind: Literal["bank", "mobile_wallet"]
    institution: StrictStr
    account_number: StrictStr = Field(max_length=40)
    change_source: Literal["onboarding", "supplier_email", "supplier_letter", "phone_call_received", "in_person", "invoice_document"]
    note: StrictStr | None = None
    validate_institution = field_validator("institution")(_text(80, min_len=2, no_links=True))
    validate_note = field_validator("note")(_text(200, min_len=1, no_links=True))

    @field_validator("account_number")
    @classmethod
    def digits_only(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9 -]+", v.strip()):
            raise ValueError("use digits only (spaces and hyphens are ignored)")
        return re.sub(r"[ -]", "", v)


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _tz(db: Session, b: UUID) -> str:
    return db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": b}).scalar_one()


def _refuse(db: Session, ctx: BusinessContext, action: str, target_type: str, target_id, code: int, message: str, **details):
    record_event(db, action=action, outcome="conflict" if code == status.HTTP_409_CONFLICT else "failed", actor=_actor(ctx),
                 business_id=ctx.business_id, target_type=target_type, target_id=target_id, details=details)
    db.commit()
    raise HTTPException(code, message)


# --- reading ---------------------------------------------------------------------------------------------------------


_FSEL = "SELECT f.* FROM fraud_flag f WHERE f.business_id = :b"


def _held(db: Session, b: UUID, flag_ids: list) -> dict:
    out: dict = {f: [] for f in flag_ids}
    if flag_ids:
        for r in db.execute(text("""SELECT h.flag_id, h.proposal_id, p.payload, p.proposed_by, u.display_name AS proposed_by_name FROM fraud_flag_hold h
                                    JOIN action_proposal p ON p.id = h.proposal_id JOIN app_user u ON u.id = p.proposed_by
                                    WHERE h.flag_id = ANY(:ids) ORDER BY h.created_at"""), {"ids": flag_ids}).mappings():
            pl = r["payload"]
            out[r["flag_id"]].append({"proposal_id": str(r["proposal_id"]), "supplier": pl["supplier"], "document_number": pl["document_number"],
                                      "amount_minor": pl["amount_minor"], "currency": pl["currency"], "from_account": pl["from_account"],
                                      "proposed_by": str(r["proposed_by"]), "proposed_by_name": r["proposed_by_name"]})
    return out


def _review_out(r) -> dict:
    return {"decision": r["decision"], "reason": r["reason"], "defer_until": r["defer_until"].isoformat() if r["defer_until"] else None,
            "verification": r["verification"], "reviewer": r["reviewer_name"], "reviewer_role": r["reviewer_role"], "at": r["created_at"].isoformat()}


def _summary(row, reviews: list, held: list, today: date) -> dict:
    st = flag_state(reviews)
    last = reviews[-1] if reviews else None
    return {"id": str(row["id"]), "rule": row["rule"], "rules_version": row["rules_version"], "title": row["title"], "severity": row["severity"],
            "reason": row["reason"], "currency": row["currency"], "amount_minor": row["amount_minor"], "state": st,
            "review_due": st == "deferred" and last is not None and last["defer_until"] <= today,
            "subject_refs": row["subject_refs"], "compared_refs": row["compared_refs"], "evidence": row["evidence"],
            "holds": held, "holding": st != "cleared" and bool(held), "created_at": row["created_at"].isoformat(),
            "verdict": "Suspected activity for review. This is not a finding of fraud."}


def _load_flag(db: Session, ctx: BusinessContext, flag_id: UUID, lock: bool = False):
    row = db.execute(text(_FSEL + " AND f.id = :i" + (" FOR UPDATE" if lock else "")), {"b": ctx.business_id, "i": flag_id}).mappings().first()
    if row is None:  # includes another business's flag
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    return row


def _recorder(db: Session, row) -> UUID | None:
    if row["destination_id"] is None:
        return None
    return db.execute(text("SELECT recorded_by FROM payee_destination WHERE id = :d"), {"d": row["destination_id"]}).scalar_one()


def _can(ctx: BusinessContext, row, st: str, held: list, recorder: UUID | None) -> dict:
    unresolved = st in UNRESOLVED
    needs = []
    blocked = None
    if held:
        needs.append("An owner who did not propose the held payment")
        if ctx.role != "owner":
            blocked = "Only an owner can clear a flag that holds a payment."
        elif any(h["proposed_by"] == str(ctx.user.id) for h in held):
            blocked = "You proposed a payment this flag holds, so another owner must clear it."
    if row["rule"] == "destination_change":
        needs.append("Independent verification on a contact already on file before the change, by someone other than the person who recorded it")
        if recorder == ctx.user.id:
            blocked = "You recorded this destination change, so someone else must verify it."
    return {"confirm": unresolved, "defer": unresolved, "reject": unresolved and blocked is None, "reject_blocked_reason": blocked if unresolved else None,
            "reject_needs": needs, "verification_required": row["rule"] == "destination_change"}


@router.get("/rules")
def rules_info(ctx: BusinessContext = Depends(require("fraud.read"))) -> dict:
    return {"version": rules.RULES_VERSION,
            "rules": [{"rule": k, "title": v["title"], "severity": v["severity"], "description": v["describe"], "outcomes": v["outcomes"]}
                      for k, v in rules.RULES.items()],
            "parameters": {"duplicate_days": rules.DUPLICATE_DAYS, "history_days": rules.HISTORY_DAYS, "unusual_multiple": rules.UNUSUAL_MULTIPLE,
                           "unusual_min_history": rules.UNUSUAL_MIN_HISTORY, "split_days": rules.SPLIT_DAYS, "split_near_percent": rules.SPLIT_NEAR_PERCENT,
                           "split_limits_minor": rules.SPLIT_LIMITS, "lookalike_similarity_percent": rules.LOOKALIKE_SIMILARITY_PERCENT,
                           "settlement_lag_days": rules.SETTLEMENT_LAG_DAYS, "settlement_match_days": rules.SETTLEMENT_MATCH_DAYS},
            "verification_methods": VERIFICATION_METHODS, "change_sources": CHANGE_SOURCES, "max_defer_days": MAX_DEFER_DAYS,
            "note": "A flag is suspected activity for a person to review, never a fraud verdict. Holds stop a proposed sandbox payment from being approved or released; nothing is paid or blocked at a bank."}


@router.get("/flags")
def list_flags(state_filter: Annotated[Literal["unresolved", "open", "deferred", "confirmed", "cleared", "all"], Query(alias="state")] = "all",
               ctx: BusinessContext = Depends(require("fraud.read")), db: Session = Depends(get_session)) -> dict:
    today = business_today(_tz(db, ctx.business_id))
    rows = db.execute(text(_FSEL + " ORDER BY f.created_at DESC LIMIT 200"), {"b": ctx.business_id}).mappings().all()
    ids = [r["id"] for r in rows]
    revs, held = reviews_for(db, ids), _held(db, ctx.business_id, ids)
    items = [_summary(r, revs[r["id"]], held[r["id"]], today) for r in rows]
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("open", "deferred", "confirmed", "cleared")}
    if state_filter == "unresolved":
        items = [i for i in items if i["state"] in UNRESOLVED]
    elif state_filter != "all":
        items = [i for i in items if i["state"] == state_filter]
    sev = {"high": 0, "medium": 1}
    order = {"open": 0, "deferred": 1, "confirmed": 2, "cleared": 3}
    items.sort(key=lambda i: (order[i["state"]], not i["holding"], sev[i["severity"]], i["created_at"]), reverse=False)
    return {"items": items, "counts": counts}


@router.get("/overview")
def overview(ctx: BusinessContext = Depends(require("fraud.read")), db: Session = Depends(get_session)) -> dict:
    tz = _tz(db, ctx.business_id)
    proposals = open_payment_proposals(db, ctx.business_id, tz, utc_now())
    holds = holds_for(db, ctx.business_id, [UUID(p["id"]) for p in proposals])
    held = [p | {"flags": holds[UUID(p["id"])]} for p in proposals if holds[UUID(p["id"])]]
    states = db.execute(text("""SELECT f.id, coalesce(array_agg(r.decision ORDER BY r.created_at) FILTER (WHERE r.id IS NOT NULL), '{}') AS decisions
                                FROM fraud_flag f LEFT JOIN fraud_review r ON r.flag_id = f.id WHERE f.business_id = :b GROUP BY f.id"""),
                        {"b": ctx.business_id}).mappings().all()
    counts = {s: 0 for s in ("open", "deferred", "confirmed", "cleared")}
    for r in states:
        counts[flag_state([{"decision": d} for d in r["decisions"]])] += 1
    last = db.execute(text("""SELECT s.*, u.display_name FROM fraud_scan s JOIN app_user u ON u.id = s.created_by
                              WHERE s.business_id = :b ORDER BY s.created_at DESC LIMIT 1"""), {"b": ctx.business_id}).mappings().first()
    return {"rules_version": rules.RULES_VERSION, "counts": counts, "unresolved": counts["open"] + counts["deferred"], "held_payments": held,
            "last_scan": None if last is None else {"at": last["created_at"].isoformat(), "as_of": last["as_of"].isoformat(), "trigger": last["trigger"],
                                                    "by": last["display_name"], "flags_created": last["flags_created"]}}


@router.get("/transactions")
def recent_transactions(days: int = Query(30, ge=1, le=180), ctx: BusinessContext = Depends(require("fraud.read")),
                        db: Session = Depends(get_session)) -> dict:
    """Recent outgoing payments (posted) and open payment proposals, with the flag that concerns each, for the left-hand table."""
    b = ctx.business_id
    tz = _tz(db, b)
    since = business_today(tz) - timedelta(days=days)
    flagged: dict[str, list[str]] = {}
    rows = db.execute(text(_FSEL + " AND NOT fraud_flag_cleared(f.id)"), {"b": b}).mappings().all()
    revs = reviews_for(db, [r["id"] for r in rows])
    for r in rows:
        if flag_state(revs[r["id"]]) in UNRESOLVED:
            for ref in r["subject_refs"]:
                flagged.setdefault(ref["id"], []).append(str(r["id"]))
    ledger = db.execute(text(
        """SELECT lt.id, lt.business_date, lt.amount_minor, lt.currency, lt.counterparty, lt.description, lt.reference, lt.account_id, ca.label
           FROM ledger_transaction lt JOIN cash_account ca ON ca.id = lt.account_id
           WHERE lt.business_id = :b AND lt.amount_minor < 0 AND lt.business_date >= :since
             AND NOT EXISTS (SELECT 1 FROM own_transfer t WHERE t.voided_at IS NULL AND t.out_txn_id = lt.id)
           ORDER BY lt.business_date DESC, lt.posted_at DESC LIMIT 100"""), {"b": b, "since": since}).mappings().all()
    items = [{"type": "ledger_transaction", "id": str(r["id"]), "date": r["business_date"].isoformat(), "payee": masked(r["counterparty"] or r["description"]),
              "amount_minor": -r["amount_minor"], "currency": r["currency"], "reference": masked(r["reference"]), "account_id": str(r["account_id"]),
              "account": r["label"], "status": "review" if str(r["id"]) in flagged else "posted", "flag_ids": flagged.get(str(r["id"]), [])} for r in ledger]
    proposals = open_payment_proposals(db, b, tz, utc_now())
    holds = holds_for(db, b, [UUID(p["id"]) for p in proposals])
    for p in proposals:
        h = holds[UUID(p["id"])]
        items.append({"type": "action_proposal", "id": p["id"], "date": p["date"], "payee": p["payee"], "amount_minor": p["amount_minor"],
                      "currency": p["currency"], "reference": p["reference"], "account_id": p["account_id"], "account": p["account"],
                      "status": "on_hold" if h else "proposed", "proposal_state": p["status"],
                      "flag_ids": [x["flag_id"] for x in h] or flagged.get(p["id"], [])})
    items.sort(key=lambda i: (i["date"], i["type"] == "action_proposal"), reverse=True)
    return {"items": items[:50], "since": since.isoformat()}


@router.get("/flags/{flag_id}")
def get_flag(flag_id: UUID, ctx: BusinessContext = Depends(require("fraud.read")), db: Session = Depends(get_session)) -> dict:
    row = _load_flag(db, ctx, flag_id)
    today = business_today(_tz(db, ctx.business_id))
    revs = reviews_for(db, [row["id"]])[row["id"]]
    held = _held(db, ctx.business_id, [row["id"]])[row["id"]]
    out = _summary(row, revs, held, today)
    for h in out["holds"]:
        h["held_now"] = out["holding"]
    return out | {"reviews": [_review_out(r) for r in revs], "cash_outcomes": cash_outcomes(db, ctx.business_id, out, out["state"]),
                  "can": _can(ctx, row, out["state"], held, _recorder(db, row)), "outcome_labels": rules.RULES[row["rule"]]["outcomes"]}


# --- writing ---------------------------------------------------------------------------------------------------------


@router.post("/scans", status_code=status.HTTP_201_CREATED)
def scan(ctx: BusinessContext = Depends(require("fraud.run")), db: Session = Depends(get_session)) -> dict:
    res = run_scan(db, ctx.business_id, ctx.user.id, "manual")
    record_event(db, action="fraud.scan", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="fraud_scan",
                 target_id=res["scan_id"], details={"rules_version": res["rules_version"], "as_of": res["as_of"], "items_checked": res["items_checked"],
                                                    "flags_created": res["flags_created"], "flags_existing": res["flags_existing"], "holds_added": res["holds_added"]})
    db.commit()
    return res


@router.post("/flags/{flag_id}/reviews")
def review(flag_id: UUID, body: ReviewIn, ctx: BusinessContext = Depends(require("fraud.review")), db: Session = Depends(get_session)) -> dict:
    row = _load_flag(db, ctx, flag_id, lock=True)
    tz = _tz(db, ctx.business_id)
    today = business_today(tz)
    st = flag_state(reviews_for(db, [row["id"]])[row["id"]])
    held = _held(db, ctx.business_id, [row["id"]])[row["id"]]
    act, refuse = "fraud.review", lambda code, msg, why: _refuse(db, ctx, "fraud.review", "fraud_flag", row["id"], code, msg, rule=row["rule"],
                                                                     decision=body.decision, reason=why)
    if st not in UNRESOLVED:
        refuse(status.HTTP_409_CONFLICT, f"This flag is already {st}; a final review cannot be changed.", f"state {st}")
    if body.decision == "defer":
        if body.defer_until is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give the date to review this flag again.")
        if not today < body.defer_until <= today + timedelta(days=MAX_DEFER_DAYS):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"The review date must be after today and within {MAX_DEFER_DAYS} days.")
    elif body.defer_until is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A review date is only used when deferring.")
    verification = None
    if body.decision == "reject":
        if held and ctx.role != "owner":
            refuse(status.HTTP_403_FORBIDDEN, "Only an owner can clear a flag that holds a payment.", "not owner")
        if any(h["proposed_by"] == str(ctx.user.id) for h in held):
            refuse(status.HTTP_409_CONFLICT, "You proposed a payment this flag holds, so another owner must clear it.", "proposer clearing own hold")
        if row["rule"] == "destination_change":
            if _recorder(db, row) == ctx.user.id:
                refuse(status.HTTP_409_CONFLICT, "You recorded this destination change, so someone else must verify it independently.", "recorder verifying own change")
            if body.verification is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Clearing a destination change needs an independent verification.")
            if not body.verification.contact_predates_change:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "Use a contact that was on file before the change. Details in the change request itself are not independent.")
        if body.verification is not None:
            changed_on = row["created_at"].astimezone(ZoneInfo(tz)).date() if row["rule"] == "destination_change" else date.min
            if not changed_on <= body.verification.checked_on <= today:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "The verification date must be between the date of the change and today." if row["rule"] == "destination_change"
                                    else "The verification date cannot be in the future.")
            verification = body.verification.model_dump(mode="json") | {"method_label": VERIFICATION_METHODS[body.verification.method]}
    elif body.verification is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A verification is recorded only when clearing a flag.")
    decision = {"confirm": "confirmed", "reject": "rejected", "defer": "deferred"}[body.decision]
    db.execute(text("""INSERT INTO fraud_review (business_id, flag_id, decision, reason, defer_until, verification, reviewer_id, reviewer_role)
                       VALUES (:b, :f, :d, :r, :u, CAST(:v AS jsonb), :rv, :rr)"""),
               {"b": ctx.business_id, "f": row["id"], "d": decision, "r": body.reason, "u": body.defer_until,
                "v": canonical(verification) if verification else None, "rv": ctx.user.id, "rr": ctx.role})
    record_event(db, action=act, outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="fraud_flag", target_id=row["id"],
                 details={"rule": row["rule"], "decision": decision, "reason": body.reason, "defer_until": body.defer_until.isoformat() if body.defer_until else None,
                          "verification_method": verification["method"] if verification else None, "held_payments": len(held),
                          "hold_released": decision == "rejected" and bool(held)})
    db.commit()
    return get_flag(flag_id, ctx, db)


@router.get("/payee-destinations")
def destinations(ctx: BusinessContext = Depends(require("fraud.read")), db: Session = Depends(get_session)) -> dict:
    """Suppliers (parties with a bill) and any party with a recorded destination, with its masked destination history."""
    tz = _tz(db, ctx.business_id)
    parties = db.execute(text(
        """SELECT p.id, p.name FROM party p WHERE p.business_id = :b
             AND (EXISTS (SELECT 1 FROM obligation o WHERE o.party_id = p.id AND o.direction = 'payable')
                  OR EXISTS (SELECT 1 FROM payee_destination d WHERE d.party_id = p.id))
           ORDER BY lower(p.name)"""), {"b": ctx.business_id}).mappings().all()
    hist: dict = {p["id"]: [] for p in parties}
    for d in db.execute(text("""SELECT d.*, u.display_name FROM payee_destination d JOIN app_user u ON u.id = d.recorded_by
                                WHERE d.business_id = :b ORDER BY d.created_at, d.id"""), {"b": ctx.business_id}).mappings():
        hist.setdefault(d["party_id"], []).append({
            "id": str(d["id"]), "kind": d["kind"], "institution": d["institution"], "masked": f"••••{d['last4']}", "change_source": d["change_source"],
            "note": d["note"], "recorded_by": d["display_name"], "recorded_at": d["created_at"].isoformat(),
            "date": d["created_at"].astimezone(ZoneInfo(tz)).date().isoformat()})
    return {"parties": [{"party_id": str(p["id"]), "name": p["name"], "current": (hist[p["id"]] or [None])[-1],
                         "history": list(reversed(hist[p["id"]]))} for p in parties],
            "change_sources": CHANGE_SOURCES, "note": "Only the last four digits are kept. The full number is never stored or shown."}


@router.post("/payee-destinations", status_code=status.HTTP_201_CREATED)
def record_destination(body: DestinationIn, ctx: BusinessContext = Depends(require("payees.destinations.write")), db: Session = Depends(get_session)) -> dict:
    digits = body.account_number
    if body.kind == "bank" and not 6 <= len(digits) <= 20:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "account_number"], "msg": "A bank account number has 6 to 20 digits.", "type": "value_error"}])
    if body.kind == "mobile_wallet" and not re.fullmatch(r"01[3-9]\d{8}", digits):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "account_number"], "msg": "A mobile wallet number has 11 digits and starts with 013-019.", "type": "value_error"}])
    party = db.execute(text("SELECT id, name FROM party WHERE business_id = :b AND id = :p FOR UPDATE"), {"b": ctx.business_id, "p": body.party_id}).mappings().first()
    if party is None:  # includes another business's party
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", "party_id"], "msg": "That payee was not found.", "type": "value_error"}])
    digest = destination_hmac(ctx.business_id, body.kind, digits)
    current = db.execute(text("""SELECT kind, number_hmac FROM payee_destination WHERE business_id = :b AND party_id = :p
                                 ORDER BY created_at DESC, id DESC LIMIT 1"""), {"b": ctx.business_id, "p": party["id"]}).mappings().first()
    if current and current["kind"] == body.kind and current["number_hmac"] == digest:
        raise HTTPException(status.HTTP_409_CONFLICT, f"That is already the current destination for {party['name']}.")
    did = db.execute(text(
        """INSERT INTO payee_destination (business_id, party_id, kind, institution, last4, number_hmac, change_source, note, recorded_by, recorded_role)
           VALUES (:b, :p, :k, :i, :l4, :h, :s, :n, :u, :r) RETURNING id"""),
        {"b": ctx.business_id, "p": party["id"], "k": body.kind, "i": body.institution, "l4": digits[-4:], "h": digest, "s": body.change_source,
         "n": body.note, "u": ctx.user.id, "r": ctx.role}).scalar_one()
    res = run_scan(db, ctx.business_id, ctx.user.id, "destination")
    flag = db.execute(text("SELECT id FROM fraud_flag WHERE business_id = :b AND destination_id = :d"), {"b": ctx.business_id, "d": did}).scalar_one_or_none()
    record_event(db, action="payees.destination_record", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="payee_destination",
                 target_id=did, details={"party": party["name"], "kind": body.kind, "institution": body.institution, "account_number": digits,
                                         "change_source": body.change_source, "is_change": current is not None, "flag_created": flag is not None,
                                         "holds_added": res["holds_added"]})
    db.commit()
    return {"id": str(did), "party_id": str(party["id"]), "party": party["name"], "kind": body.kind, "institution": body.institution,
            "masked": f"••••{digits[-4:]}", "is_change": current is not None, "flag_id": str(flag) if flag else None, "scan": res}

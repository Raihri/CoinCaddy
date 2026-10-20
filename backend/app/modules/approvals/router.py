"""Approved Action Workflow API: propose an exact action, approve or reject it under its tier, and release it as executable.

Nothing here sends a reminder, moves money, or contacts a lender. "Executable" only records that every gate was
re-checked immediately before release; the sandbox executor (a later prompt) must check again before it acts.
"""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, Union
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_session
from app.modules.approvals.policy import (KINDS, POLICY_VERSION, REAUTH_MAX_FAILURES, REAUTH_WINDOW, REQUIRED_ROLE, TIERS,
                                          VERIFICATION_METHODS, envelope, iso, proposal_hash, state)
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import clean_text
from app.modules.identity.deps import BusinessContext, require
from app.modules.identity.passwords import verify_password
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import canonical, sha256
from app.modules.records.normalize import looks_like_formula
from app.modules.readiness.engine import fmt
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/approvals", tags=["approvals"])
LONG_NUMBER = re.compile(r"\d{8}")
# A calendar date such as 2026-10-05 is not an account number; it is removed before the long-number check.
ISO_DATE = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
LINK = re.compile(r"https?://|www\.", re.IGNORECASE)
HASH = r"^[0-9a-f]{64}$"


def _text(max_len: int, min_len: int = 1, no_links: bool = False):
    def check(v: str | None) -> str | None:
        if v is None:
            return v
        v = clean_text(v, max_len)
        if len(v) < min_len:
            raise ValueError(f"must be at least {min_len} characters")
        if looks_like_formula(v):
            raise ValueError("must not look like a spreadsheet formula")
        if LONG_NUMBER.search(re.sub(r"[\s-]", "", ISO_DATE.sub(" ", v))):
            raise ValueError("must not contain an account, card, or ID number")
        if no_links and LINK.search(v):
            raise ValueError("must not contain links")
        return v
    return check


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ProposalBase(_Strict):
    expires_in_hours: StrictInt | None = Field(default=None, ge=1, le=72)


class ReminderIn(_ProposalBase):
    kind: Literal["invoice_reminder"]
    obligation_id: UUID
    channel: Literal["email", "sms"]
    message: StrictStr
    validate_message = field_validator("message")(_text(500, min_len=10, no_links=True))


class PaymentIn(_ProposalBase):
    kind: Literal["supplier_payment"]
    obligation_id: UUID
    amount_minor: StrictInt = Field(gt=0, le=MAX_AMOUNT_MINOR)
    from_account_id: UUID
    payment_review_id: UUID | None = None


class ApplicationIn(_ProposalBase):
    kind: Literal["financing_application"]
    readiness_check_id: UUID


ProposalIn = Annotated[Union[ReminderIn, PaymentIn, ApplicationIn], Field(discriminator="kind")]


class VerificationIn(_Strict):
    method: Literal["called_known_contact", "met_in_person", "written_confirmation"]
    reference: StrictStr
    checked_on: date
    validate_ref = field_validator("reference")(_text(120, min_len=3))


class DecisionIn(_Strict):
    decision: Literal["approve", "reject"]
    payload_sha256: StrictStr = Field(pattern=HASH)
    reason: StrictStr | None = None
    password: StrictStr | None = Field(default=None, max_length=200)
    verification: VerificationIn | None = None
    validate_reason = field_validator("reason")(_text(300, min_len=3))


class WithdrawIn(_Strict):
    payload_sha256: StrictStr = Field(pattern=HASH)
    reason: StrictStr
    validate_reason = field_validator("reason")(_text(300, min_len=3))


class ReleaseIn(_Strict):
    payload_sha256: StrictStr = Field(pattern=HASH)


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _today(db: Session, business_id: UUID) -> date:
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


class SourceError(Exception):
    pass


# --- payloads, built from the business's own records (the client only names the source) ---------------------


def _others(db: Session, b: UUID, kind: str, key: str, value: str, self_id, released_only: bool) -> list[dict]:
    """Payloads of the business's other proposals of `kind` with payload[key] == value that still count: released
    (executable) ones always; pending or approved ones until they expire, unless only released ones are asked for."""
    rows = db.execute(text(
        """SELECT p.payload, p.expires_at, array_remove(array_agg(d.event), NULL) AS events
           FROM action_proposal p LEFT JOIN action_decision d ON d.proposal_id = p.id
           WHERE p.business_id = :b AND p.kind = :k AND p.payload ->> :key = :v AND p.id IS DISTINCT FROM CAST(:self AS uuid)
           GROUP BY p.id"""), {"b": b, "k": kind, "key": key, "v": value, "self": self_id}).mappings().all()
    now = utc_now()
    out = []
    for r in rows:
        events = set(r["events"])
        if "executable" in events:
            out.append(r["payload"])
        elif not released_only and not events & {"rejected", "withdrawn"} and now < r["expires_at"]:
            out.append(r["payload"])
    return out


def _obligation(db: Session, b: UUID, today: date, obligation_id: str, direction: str) -> dict:
    # Lock the invoice or bill so two proposals (or releases) for it are checked one after the other.
    db.execute(text("SELECT id FROM obligation WHERE business_id = :b AND id = :o FOR UPDATE"), {"b": b, "o": obligation_id})
    found = list_obligations(db, b, today, obligation_id=UUID(obligation_id))
    if not found or found[0]["direction"] != direction:  # includes another business's obligation
        raise SourceError("That invoice was not found." if direction == "receivable" else "That bill was not found.")
    o = found[0]
    if o["status"] not in ("open", "partially_settled"):
        raise SourceError(f"{o['document_number']} is {o['status'].replace('_', ' ')}; nothing is outstanding.")
    if o["disputed"]:
        raise SourceError(f"{o['document_number']} is disputed; resolve the dispute first.")
    return o


def _reminder(db: Session, b: UUID, today: date, p: dict, self_id=None, release: bool = False) -> dict:
    o = _obligation(db, b, today, p["obligation_id"], "receivable")
    if not o["overdue"]:
        raise SourceError(f"{o['document_number']} is not overdue (due {o['due_date']}); only an overdue invoice gets a reminder.")
    if _others(db, b, "invoice_reminder", "obligation_id", o["id"], self_id, released_only=release):
        raise SourceError(f"A reminder for {o['document_number']} is already released." if release else
                          f"A reminder proposal for {o['document_number']} is already open; decide or withdraw it first.")
    return {"obligation_id": o["id"], "document_number": o["document_number"], "customer": o["party"]["name"],
            "outstanding_minor": o["outstanding_minor"], "currency": o["currency"], "due_date": o["due_date"],
            "channel": p["channel"], "message": p["message"], "delivery": "sandbox"}


def _payment(db: Session, b: UUID, today: date, p: dict, self_id=None, release: bool = False) -> dict:
    o = _obligation(db, b, today, p["obligation_id"], "payable")
    cur, out = o["currency"], o["outstanding_minor"]
    if p["amount_minor"] > out:
        raise SourceError(f"The amount is more than the {fmt(out, cur)} still outstanding on {o['document_number']}.")
    # Payments already proposed for the same bill count against what is outstanding, so together they never pay more.
    committed = sum(x["amount_minor"] for x in _others(db, b, "supplier_payment", "obligation_id", o["id"], self_id, released_only=release))
    if committed + p["amount_minor"] > out:
        held = "released" if release else "open"
        raise SourceError(f"Other {held} payment proposals for {o['document_number']} already cover {fmt(committed, cur)} of the {fmt(out, cur)} "
                          f"outstanding; at most {fmt(max(out - committed, 0), cur)} more can be paid.")
    acc = db.execute(text("SELECT id, label, kind, currency FROM cash_account WHERE business_id = :b AND id = :i"),
                     {"b": b, "i": p["from_account_id"]}).mappings().first()
    if acc is None:
        raise SourceError("That account was not found.")
    if acc["kind"] not in ("bank", "mobile_wallet"):
        raise SourceError("A sandbox payment can only be made from a bank or mobile wallet account.")
    if acc["currency"] != cur:
        raise SourceError(f"The account is in {acc['currency']} but the bill is in {cur}.")
    review = None
    if p.get("payment_review_id"):
        r = db.execute(text("SELECT id, request, decision, as_of FROM payment_review WHERE business_id = :b AND id = :i"),
                       {"b": b, "i": p["payment_review_id"]}).mappings().first()
        if r is None:
            raise SourceError("That Payment Review was not found.")
        if r["request"]["amount_minor"] != p["amount_minor"] or r["request"]["account_id"] != str(acc["id"]):
            raise SourceError("The linked Payment Review is for a different amount or account.")
        review = {"id": str(r["id"]), "decision": r["decision"], "as_of": r["as_of"].isoformat()}
    return {"obligation_id": o["id"], "document_number": o["document_number"], "supplier": o["party"]["name"],
            "amount_minor": p["amount_minor"], "currency": cur, "outstanding_minor": out,
            "from_account_id": str(acc["id"]), "from_account": acc["label"], "payment_review_id": review["id"] if review else None,
            "payment_review": review, "delivery": "sandbox"}


def _application(db: Session, b: UUID, today: date, p: dict, self_id=None, release: bool = False) -> dict:
    c = db.execute(text("SELECT id, run_id, product_id, state, results, created_at FROM readiness_check WHERE business_id = :b AND id = :i FOR UPDATE"),
                   {"b": b, "i": p["readiness_check_id"]}).mappings().first()
    if c is None:
        raise SourceError("That readiness check was not found.")
    if c["state"] != "ready":
        raise SourceError("Only a readiness check with nothing missing or uncertain can become an application.")
    latest = db.execute(text("SELECT id FROM readiness_check WHERE business_id = :b AND run_id = :r AND product_id = :p ORDER BY created_at DESC LIMIT 1"),
                        {"b": b, "r": c["run_id"], "p": c["product_id"]}).scalar_one()
    if latest != c["id"]:
        raise SourceError("A newer readiness check exists for this product; use the latest one.")
    changed = db.execute(text("SELECT count(*) FROM business_document WHERE business_id = :b AND (created_at > :t OR voided_at > :t)"),
                         {"b": b, "t": c["created_at"]}).scalar_one()
    if changed:
        raise SourceError("The document register changed after this readiness check; check readiness again.")
    prod = c["results"]["product"]
    same = [x for x in _others(db, b, "financing_application", "product_id", c["product_id"], self_id, released_only=release)
            if x["run_id"] == str(c["run_id"])]
    if same:
        raise SourceError(f"An application for {prod['name']} is already released." if release else
                          f"An application proposal for {prod['name']} is already open; decide or withdraw it first.")
    req = db.execute(text("SELECT request FROM funding_match_run WHERE business_id = :b AND id = :r"), {"b": b, "r": c["run_id"]}).scalar_one()
    return {"readiness_check_id": str(c["id"]), "run_id": str(c["run_id"]), "product_id": c["product_id"], "product": prod["name"],
            "provider": prod["provider"], "amount_minor": req["amount_minor"], "currency": req["currency"], "needed_by": req["needed_by"],
            "purpose": req["purpose"], "illustrative": True, "recipient": "sandbox lender", "delivery": "sandbox"}


BUILDERS = {"invoice_reminder": _reminder, "supplier_payment": _payment, "financing_application": _application}
# Facts the release recheck compares with today's records (what an approver relied on).
FACTS = {"invoice_reminder": ("obligation_id", "customer", "outstanding_minor", "currency"),
         "supplier_payment": ("obligation_id", "supplier", "amount_minor", "currency", "from_account_id"),
         "financing_application": ("readiness_check_id", "product_id", "amount_minor", "currency")}


# --- reading ------------------------------------------------------------------------------------------------

_PSEL = """SELECT p.*, u.display_name AS proposed_by_name FROM action_proposal p JOIN app_user u ON u.id = p.proposed_by WHERE p.business_id = :b"""


def _decisions(db: Session, proposal_ids: list) -> dict:
    out: dict = {i: [] for i in proposal_ids}
    if proposal_ids:
        for d in db.execute(text("""SELECT d.*, u.display_name AS actor_name FROM action_decision d JOIN app_user u ON u.id = d.actor_id
                                    WHERE d.proposal_id = ANY(:ids) ORDER BY d.created_at"""), {"ids": proposal_ids}).mappings().all():
            out[d["proposal_id"]].append(d)
    return out


def _decision_out(d) -> dict:
    return {"event": d["event"], "actor": d["actor_name"], "actor_role": d["actor_role"], "at": iso(d["created_at"]), "reason": d["reason"],
            "payload_sha256": d["payload_sha256"], "reauthenticated": d["reauthenticated_at"] is not None,
            "verification": d["verification"], "checks": d["checks"]}


def _out(row, decisions: list, ctx: BusinessContext, now: datetime) -> dict:
    st = state(row, decisions, now)
    approved = next((d for d in decisions if d["event"] == "approved"), None)
    is_owner = ctx.role == REQUIRED_ROLE
    own = row["proposed_by"] == ctx.user.id
    return {
        "id": str(row["id"]), "kind": row["kind"], "title": KINDS[row["kind"]]["title"], "tier": row["tier"],
        "required_role": row["required_role"], "second_reviewer_required": row["second_reviewer_required"],
        "policy_version": row["policy_version"], "gates": TIERS[row["tier"]]["gates"],
        "payload": row["payload"], "payload_sha256": row["payload_sha256"],
        "integrity": {"payload_hash_matches": proposal_hash(row) == row["payload_sha256"]},
        "proposed_by": {"id": str(row["proposed_by"]), "name": row["proposed_by_name"], "role": row["proposed_role"]},
        "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"]),
        "state": st, "expired_after_approval": st == "expired" and approved is not None,
        "decisions": [_decision_out(d) for d in decisions],
        "can": {"approve": is_owner and st == "pending" and not (row["second_reviewer_required"] and own),
                "reject": is_owner and st == "pending",
                "withdraw": st == "pending" and (own or is_owner),
                "release": is_owner and st == "approved"},
        "executed": False,
    }


def _load(db: Session, ctx: BusinessContext, proposal_id: UUID, lock: bool = False):
    row = db.execute(text(_PSEL + " AND p.id = :i" + (" FOR UPDATE OF p" if lock else "")), {"b": ctx.business_id, "i": proposal_id}).mappings().first()
    if row is None:  # includes a proposal of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposal not found")
    return row


def _refuse(db: Session, ctx: BusinessContext, action: str, row, code: int, message: str, **details):
    record_event(db, action=action, outcome="conflict" if code == status.HTTP_409_CONFLICT else "failed", actor=_actor(ctx),
                 business_id=ctx.business_id, target_type="action_proposal", target_id=row["id"],
                 details={"kind": row["kind"], "tier": row["tier"], **details})
    db.commit()
    raise HTTPException(code, message)


@router.get("/policy")
def policy(ctx: BusinessContext = Depends(require("approvals.read"))) -> dict:
    return {"version": POLICY_VERSION, "required_role": REQUIRED_ROLE, "kinds": KINDS, "tiers": TIERS, "verification_methods": VERIFICATION_METHODS,
            "executor": "not built: approval only marks an action executable; nothing is sent or paid"}


@router.get("")
def list_proposals(state_filter: Annotated[Literal["pending", "approved", "executable", "rejected", "withdrawn", "expired", "open"] | None, Query(alias="state")] = None,
                   ctx: BusinessContext = Depends(require("approvals.read")), db: Session = Depends(get_session)) -> dict:
    now = utc_now()
    rows = db.execute(text(_PSEL + " ORDER BY p.created_at DESC LIMIT 100"), {"b": ctx.business_id}).mappings().all()
    decisions = _decisions(db, [r["id"] for r in rows])
    items = [_out(r, decisions[r["id"]], ctx, now) for r in rows]
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("pending", "approved", "executable", "rejected", "withdrawn", "expired")}
    if state_filter == "open":
        items = [i for i in items if i["state"] in ("pending", "approved")]
    elif state_filter:
        items = [i for i in items if i["state"] == state_filter]
    return {"items": items, "counts": counts}


@router.get("/{proposal_id}")
def get_proposal(proposal_id: UUID, ctx: BusinessContext = Depends(require("approvals.read")), db: Session = Depends(get_session)) -> dict:
    row = _load(db, ctx, proposal_id)
    return _out(row, _decisions(db, [row["id"]])[row["id"]], ctx, utc_now())


# --- writing ------------------------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def propose(body: ProposalIn, ctx: BusinessContext = Depends(require("approvals.propose")), db: Session = Depends(get_session)) -> dict:
    kind = body.kind
    tier = KINDS[kind]["tier"]
    rules = TIERS[tier]
    hours = body.expires_in_hours or rules["default_hours"]
    if hours > rules["max_hours"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"A {tier} proposal can be valid for at most {rules['max_hours']} hours.")
    params = body.model_dump(mode="json", exclude={"kind", "expires_in_hours"})
    try:
        payload = BUILDERS[kind](db, ctx.business_id, _today(db, ctx.business_id), params)
    except SourceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    created = utc_now().replace(microsecond=0)
    expires = created + timedelta(hours=hours)
    env = envelope(business_id=ctx.business_id, kind=kind, tier=tier, required_role=REQUIRED_ROLE, second_reviewer_required=rules["second_reviewer_required"],
                   policy_version=POLICY_VERSION, proposed_by=ctx.user.id, created_at=created, expires_at=expires, payload=payload)
    digest = sha256(env)
    pid = db.execute(text(
        """INSERT INTO action_proposal (business_id, kind, tier, required_role, second_reviewer_required, policy_version, payload, payload_sha256,
                                        proposed_by, proposed_role, created_at, expires_at)
           VALUES (:b, :k, :t, :r, :s, :v, CAST(:p AS jsonb), :h, :u, :ur, :c, :e) RETURNING id"""),
        {"b": ctx.business_id, "k": kind, "t": tier, "r": REQUIRED_ROLE, "s": rules["second_reviewer_required"], "v": POLICY_VERSION,
         "p": canonical(payload), "h": digest, "u": ctx.user.id, "ur": ctx.role, "c": created, "e": expires}).scalar_one()
    record_event(db, action="approvals.propose", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                 target_id=pid, details={"kind": kind, "tier": tier, "expires_at": iso(expires), "payload_sha256": digest[:16]})
    db.commit()
    row = _load(db, ctx, pid)
    return _out(row, [], ctx, utc_now())


def _reauth(db: Session, ctx: BusinessContext, row, password: str | None) -> datetime:
    failures = db.execute(text("""SELECT count(*) FROM audit_event WHERE actor_user_id = :u AND action = 'approvals.reauth'
                                  AND outcome = 'failed' AND occurred_at > :since"""), {"u": ctx.user.id, "since": utc_now() - REAUTH_WINDOW}).scalar_one()
    if failures >= REAUTH_MAX_FAILURES:
        _refuse(db, ctx, "approvals.reauth", row, status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many incorrect passwords. Wait 15 minutes before approving a T3 action.", reason="locked")
    stored = db.execute(text("SELECT password_hash FROM app_user WHERE id = :u"), {"u": ctx.user.id}).scalar_one()
    if not password or not verify_password(password, stored):
        _refuse(db, ctx, "approvals.reauth", row, status.HTTP_403_FORBIDDEN, "Re-authentication failed: the password is not correct.",
                reason="missing password" if not password else "wrong password")
    return utc_now()


@router.post("/{proposal_id}/decision")
def decide(proposal_id: UUID, body: DecisionIn, ctx: BusinessContext = Depends(require("approvals.decide")), db: Session = Depends(get_session)) -> dict:
    row = _load(db, ctx, proposal_id, lock=True)
    decisions = _decisions(db, [row["id"]])[row["id"]]
    now = utc_now()
    st = state(row, decisions, now)
    act = "approvals.decide"
    if body.payload_sha256 != row["payload_sha256"]:
        _refuse(db, ctx, act, row, status.HTTP_409_CONFLICT, "The proposal you reviewed does not match the stored proposal. Reload it and review again.",
                reason="hash mismatch")
    if proposal_hash(row) != row["payload_sha256"]:
        _refuse(db, ctx, act, row, status.HTTP_409_CONFLICT, "The stored proposal fails its integrity check; it cannot be approved.", reason="payload tampered")
    if st != "pending":
        _refuse(db, ctx, act, row, status.HTTP_409_CONFLICT,
                "This proposal has expired; propose it again." if st == "expired" else f"This proposal is already {st}.", reason=f"state {st}")
    if body.decision == "reject":
        if not body.reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Give a reason for rejecting.")
        event, reauth_at, verification = "rejected", None, None
    else:
        if row["second_reviewer_required"] and row["proposed_by"] == ctx.user.id:
            _refuse(db, ctx, act, row, status.HTTP_409_CONFLICT, "This action needs a second reviewer: you proposed it, so another owner must approve it.",
                    reason="self-approval")
        if row["tier"] == "T3":
            if body.verification is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A T3 approval needs a documented verification.")
            tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
            proposed_on = row["created_at"].astimezone(ZoneInfo(tz)).date()   # both dates in the business timezone
            if not proposed_on <= body.verification.checked_on <= business_today(tz):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    f"The verification date must be between the proposal date ({proposed_on.isoformat()}) and today.")
            reauth_at = _reauth(db, ctx, row, body.password)
            verification = body.verification.model_dump(mode="json") | {"method_label": VERIFICATION_METHODS[body.verification.method]}
        else:
            if body.password is not None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Re-authentication is only used for T3 approvals.")
            reauth_at, verification = None, body.verification.model_dump(mode="json") if body.verification else None
        event = "approved"
    db.execute(text(
        """INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, reason, reauthenticated_at, verification, created_at)
           VALUES (:b, :p, :e, :u, :r, :h, :reason, :ra, CAST(:v AS jsonb), :c)"""),
        {"b": ctx.business_id, "p": row["id"], "e": event, "u": ctx.user.id, "r": ctx.role, "h": row["payload_sha256"], "reason": body.reason,
         "ra": reauth_at, "v": canonical(verification) if verification else None, "c": utc_now()})
    record_event(db, action=act, outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal", target_id=row["id"],
                 details={"kind": row["kind"], "tier": row["tier"], "decision": event, "reauthenticated": reauth_at is not None,
                          "verification_method": verification["method"] if verification else None, "payload_sha256": row["payload_sha256"][:16]})
    db.commit()
    return get_proposal(proposal_id, ctx, db)


@router.post("/{proposal_id}/withdraw")
def withdraw(proposal_id: UUID, body: WithdrawIn, ctx: BusinessContext = Depends(require("approvals.propose")), db: Session = Depends(get_session)) -> dict:
    row = _load(db, ctx, proposal_id, lock=True)
    st = state(row, _decisions(db, [row["id"]])[row["id"]], utc_now())
    if row["proposed_by"] != ctx.user.id and ctx.role != REQUIRED_ROLE:
        _refuse(db, ctx, "approvals.withdraw", row, status.HTTP_409_CONFLICT, "Only the proposer or an owner can withdraw a proposal.", reason="not proposer")
    if body.payload_sha256 != row["payload_sha256"]:
        _refuse(db, ctx, "approvals.withdraw", row, status.HTTP_409_CONFLICT, "The proposal you reviewed does not match the stored proposal.", reason="hash mismatch")
    if st != "pending":
        _refuse(db, ctx, "approvals.withdraw", row, status.HTTP_409_CONFLICT, f"Only a pending proposal can be withdrawn; this one is {st}.", reason=f"state {st}")
    db.execute(text("""INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, reason, created_at)
                       VALUES (:b, :p, 'withdrawn', :u, :r, :h, :reason, :c)"""),
               {"b": ctx.business_id, "p": row["id"], "u": ctx.user.id, "r": ctx.role, "h": row["payload_sha256"], "reason": body.reason, "c": utc_now()})
    record_event(db, action="approvals.withdraw", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                 target_id=row["id"], details={"kind": row["kind"], "tier": row["tier"], "reason": body.reason})
    db.commit()
    return get_proposal(proposal_id, ctx, db)


def _release_checks(db: Session, ctx: BusinessContext, row, decisions: list, seen_hash: str, now: datetime) -> list[dict]:
    checks: list[dict] = []

    def add(code: str, ok: bool, detail: str) -> None:
        checks.append({"code": code, "ok": bool(ok), "detail": detail})

    current_role = db.execute(text("SELECT role FROM membership WHERE business_id = :b AND user_id = :u"),
                              {"b": ctx.business_id, "u": ctx.user.id}).scalar_one_or_none()
    add("releaser_role", current_role == row["required_role"], f"Releasing user's current role: {current_role or 'none'}")
    approved = next((d for d in decisions if d["event"] == "approved"), None)
    add("approved", approved is not None, "An owner approval is recorded" if approved else "No approval is recorded")
    approver_role = db.execute(text("SELECT role FROM membership WHERE business_id = :b AND user_id = :u"),
                               {"b": ctx.business_id, "u": approved["actor_id"]}).scalar_one_or_none() if approved else None
    add("approver_role", approver_role == row["required_role"], f"Approver's current role: {approver_role or 'none'}")
    add("second_reviewer", not row["second_reviewer_required"] or (approved is not None and approved["actor_id"] != row["proposed_by"]),
        "Approved by someone other than the proposer" if row["second_reviewer_required"] else "Not required for this tier")
    add("not_expired", now < row["expires_at"], f"Valid until {iso(row['expires_at'])}")
    recomputed = proposal_hash(row)
    add("payload_integrity", recomputed == row["payload_sha256"] and (approved is None or approved["payload_sha256"] == row["payload_sha256"]) and seen_hash == row["payload_sha256"],
        "Stored payload, approved hash, and the hash you reviewed all match" if recomputed == row["payload_sha256"] == seen_hash
        else "The stored payload no longer matches the hash that was approved")
    if row["tier"] == "T3":
        add("t3_gates", approved is not None and approved["reauthenticated_at"] is not None and approved["verification"] is not None,
            "Owner re-authenticated and documented a verification" if approved and approved["verification"] else "Re-authentication or verification missing")
    p = row["payload"]
    try:
        fresh = BUILDERS[row["kind"]](db, ctx.business_id, _today(db, ctx.business_id), p, self_id=row["id"], release=True)
        changed = [k for k in FACTS[row["kind"]] if fresh.get(k) != p.get(k)]
        add("records_current", not changed, "The records still match the proposal" if not changed else f"Changed since the proposal: {', '.join(changed)}")
    except (SourceError, KeyError, TypeError, ValueError) as exc:
        add("records_current", False, str(exc) or "The proposal's source records could not be read")
    return checks


@router.post("/{proposal_id}/release")
def release(proposal_id: UUID, body: ReleaseIn, ctx: BusinessContext = Depends(require("approvals.release")), db: Session = Depends(get_session)) -> dict:
    """Re-check authorization, payload integrity, expiry, and the source records, then mark the proposal executable. Executes nothing."""
    row = _load(db, ctx, proposal_id, lock=True)
    decisions = _decisions(db, [row["id"]])[row["id"]]
    now = utc_now()
    st = state(row, decisions, now)
    if st not in ("approved", "expired"):
        _refuse(db, ctx, "approvals.release", row, status.HTTP_409_CONFLICT, f"Only an approved proposal can be released; this one is {st}.", reason=f"state {st}")
    checks = _release_checks(db, ctx, row, decisions, body.payload_sha256, now)
    failed = [c for c in checks if not c["ok"]]
    if failed:
        record_event(db, action="approvals.release", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                     target_id=row["id"], details={"kind": row["kind"], "tier": row["tier"], "failed_checks": [c["code"] for c in failed]})
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not executable: " + "; ".join(c["detail"] for c in failed), "checks": checks})
    db.execute(text("""INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, checks, created_at)
                       VALUES (:b, :p, 'executable', :u, :r, :h, CAST(:c AS jsonb), :t)"""),
               {"b": ctx.business_id, "p": row["id"], "u": ctx.user.id, "r": ctx.role, "h": row["payload_sha256"],
                "c": canonical(checks), "t": utc_now()})
    record_event(db, action="approvals.release", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                 target_id=row["id"], details={"kind": row["kind"], "tier": row["tier"], "checks_passed": len(checks), "payload_sha256": row["payload_sha256"][:16]})
    db.commit()
    return get_proposal(proposal_id, ctx, db)

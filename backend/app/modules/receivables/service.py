"""Receivables Guardian (`guardian-v1`): age outstanding invoices, suggest which customers to contact, and write
reminder drafts from the invoice's own facts.

A draft is internal. It becomes an action only through the approval workflow (an `invoice_reminder` proposal names the
draft), and it is sent only by the sandbox executor, to a verified contact. Nothing here changes an invoice: a sent
reminder never marks anything paid, and the obligations calculator is used as it is.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.modules.approvals.policy import iso, state as proposal_state
from app.modules.executor.service import summaries as execution_summaries
from app.modules.obligations.service import list_obligations
from app.modules.outlook.service import sha256
from app.modules.readiness.engine import fmt

RULES_VERSION = "guardian-v1"
REMIND_INTERVAL_DAYS = 7          # after a reminder is sent, the next one for the same invoice waits this long
BUCKETS = ("not_due", "d1_30", "d31_60", "d61_90", "d90_plus")
BUCKET_LABELS = {"not_due": "Not yet due", "d1_30": "1–30 days", "d31_60": "31–60 days", "d61_90": "61–90 days", "d90_plus": "90+ days"}
CONTACT_SOURCES = {
    "customer_form": "Customer onboarding form",
    "signed_contract": "Signed contract",
    "invoice_document": "Invoice or purchase order",
    "customer_email": "Email from the customer",
    "phone_call": "Phone call",
    "in_person": "Given in person",
}
VERIFY_METHODS = {
    "called_known_number": "Called the customer on a number already on file",
    "confirmed_in_person": "Confirmed in person",
    "signed_customer_form": "Matches a signed customer form",
}
# A draft in one of these states is the invoice's current reminder: no second draft or proposal is allowed.
ACTIVE = ("draft", "awaiting_approval", "approved", "ready_to_send", "no_answer")


class DraftProblem(Exception):
    pass


def bucket(o: dict) -> str:
    if o["disputed"]:
        return "disputed"
    d = o["days_overdue"]
    return "not_due" if d <= 0 else "d1_30" if d <= 30 else "d31_60" if d <= 60 else "d61_90" if d <= 90 else "d90_plus"


def mask(channel: str, address: str) -> str:
    if channel == "email":
        local, _, domain = address.partition("@")
        return f"{local[:2]}•••@{domain}"
    return "•••••••" + address[-4:]


def dmy(d: str | date) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime("%d %b %Y")


def local_date(ts: datetime | str, tz: str) -> date:
    ts = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    return ts.astimezone(ZoneInfo(tz)).date()


# --- verified facts and the message --------------------------------------------------------------------------


def facts_of(o: dict) -> dict:
    """The invoice facts a reminder may state. Any change to them (a payment, a cancellation) makes a draft stale."""
    return {"obligation_id": o["id"], "document_number": o["document_number"], "customer": o["party"]["name"], "party_id": o["party"]["id"],
            "issue_date": o["issue_date"], "due_date": o["due_date"], "amount_minor": o["amount_minor"],
            "outstanding_minor": o["outstanding_minor"], "currency": o["currency"]}


def message_for(facts: dict, business_name: str, note: str | None) -> str:
    cur = facts["currency"]
    text_ = (f"Dear {facts['customer']}, this is a reminder that invoice {facts['document_number']}, issued on {dmy(facts['issue_date'])} "
             f"for {fmt(facts['amount_minor'], cur)}, was due on {dmy(facts['due_date'])}. Our records show {fmt(facts['outstanding_minor'], cur)} "
             f"still outstanding. Please arrange payment, or reply if you think this is not correct. Thank you, {business_name}.")
    return text_ + (f"\n\n{note}" if note else "")


# --- contacts --------------------------------------------------------------------------------------------------

_CSEL = """SELECT c.*, u.display_name AS added_by_name, contact_state(c.id) AS state, p.name AS party_name
           FROM party_contact c JOIN app_user u ON u.id = c.added_by JOIN party p ON p.business_id = c.business_id AND p.id = c.party_id
           WHERE c.business_id = :b"""


def contact_out(c, reviews: list, user_id) -> dict:
    final = c["state"] in ("rejected", "retired")
    return {"id": str(c["id"]), "party_id": str(c["party_id"]), "party": c["party_name"], "channel": c["channel"], "address": c["address"],
            "masked": mask(c["channel"], c["address"]), "source": c["source"], "source_label": CONTACT_SOURCES[c["source"]], "state": c["state"],
            "added_by": c["added_by_name"], "added_at": iso(c["created_at"]),
            "reviews": [{"decision": r["decision"], "method": r["method"], "method_label": VERIFY_METHODS.get(r["method"] or ""),
                         "checked_on": r["checked_on"].isoformat() if r["checked_on"] else None, "reason": r["reason"],
                         "actor": r["actor_name"], "at": iso(r["created_at"])} for r in reviews],
            "can": {"verify": not final and c["state"] != "verified" and c["added_by"] != user_id,
                    "reject": not final and c["state"] != "verified" and c["added_by"] != user_id,
                    "retire": not final}}


def contacts(db: Session, b: UUID, user_id, contact_id: UUID | None = None) -> list[dict]:
    rows = db.execute(text(_CSEL + (" AND c.id = :c" if contact_id else "") + " ORDER BY p.name, c.created_at"),
                      {"b": b, "c": contact_id}).mappings().all()
    reviews: dict = defaultdict(list)
    if rows:
        for r in db.execute(text("""SELECT r.*, u.display_name AS actor_name FROM party_contact_review r JOIN app_user u ON u.id = r.actor_id
                                    WHERE r.contact_id = ANY(:ids) ORDER BY r.seq"""), {"ids": [c["id"] for c in rows]}).mappings():
            reviews[r["contact_id"]].append(r)
    return [contact_out(c, reviews[c["id"]], user_id) for c in rows]


def contact_row(db: Session, b: UUID, contact_id) -> dict | None:
    return db.execute(text(_CSEL + " AND c.id = :c"), {"b": b, "c": contact_id}).mappings().first()


def contact_check(db: Session, b: UUID, payload: dict) -> tuple[bool, str]:
    """For the executor: the reminder's contact still exists, belongs to the invoice's customer, and is verified."""
    try:
        c = contact_row(db, b, UUID(str(payload.get("contact_id"))))
    except ValueError:
        c = None
    if c is None:
        return False, "No contact is named; a reminder goes only to a verified contact"
    party = db.execute(text("SELECT party_id FROM obligation WHERE business_id = :b AND id = :o"), {"b": b, "o": payload.get("obligation_id")}).scalar_one_or_none()
    if party != c["party_id"]:
        return False, "The contact does not belong to the invoice's customer"
    if c["state"] != "verified":
        return False, f"The contact {mask(c['channel'], c['address'])} is {c['state']}; a reminder goes only to a verified contact"
    return True, f"Sends to {mask(c['channel'], c['address'])} by {'email' if c['channel'] == 'email' else 'SMS'} (verified contact of {c['party_name']})"


# --- reminder proposals and drafts ----------------------------------------------------------------------------


def reminder_proposals(db: Session, b: UUID, now: datetime, obligation_id: str | None = None) -> list[dict]:
    rows = db.execute(text(
        """SELECT p.id, p.payload, p.expires_at, p.created_at,
                  coalesce(json_agg(json_build_object('event', d.event, 'at', d.created_at, 'reason', d.reason) ORDER BY d.created_at)
                           FILTER (WHERE d.id IS NOT NULL), '[]') AS decisions
           FROM action_proposal p LEFT JOIN action_decision d ON d.proposal_id = p.id
           WHERE p.business_id = :b AND p.kind = 'invoice_reminder' AND (CAST(:o AS text) IS NULL OR p.payload ->> 'obligation_id' = CAST(:o AS text))
           GROUP BY p.id ORDER BY p.created_at"""), {"b": b, "o": obligation_id}).mappings().all()
    runs = execution_summaries(db, b, [r["id"] for r in rows])
    out = []
    for r in rows:
        st = proposal_state(r, r["decisions"], now)
        released = next((d for d in r["decisions"] if d["event"] == "executable"), None)
        rejected = next((d for d in r["decisions"] if d["event"] == "rejected"), None)
        out.append({"id": str(r["id"]), "draft_id": r["payload"].get("draft_id"), "obligation_id": r["payload"].get("obligation_id"),
                    "state": st, "created_at": iso(r["created_at"]), "expires_at": iso(r["expires_at"]),
                    "released_at": released["at"] if released else None, "rejected_reason": rejected["reason"] if rejected else None,
                    "execution": runs[r["id"]]})
    return out


def _unfinished(p: dict) -> bool:
    """Released but without a final sandbox outcome yet (not run, or no answer)."""
    return p["state"] == "executable" and (p["execution"] is None or p["execution"]["outcome"] in ("failed", "dispatched"))


def _sent(p: dict) -> bool:
    return p["state"] == "executable" and p["execution"] is not None and p["execution"]["outcome"] == "succeeded"


def draft_state(discarded: bool, latest: dict | None) -> str:
    if discarded:
        return "discarded"
    if latest is None or latest["state"] in ("withdrawn", "expired"):
        return "draft"
    if latest["state"] == "pending":
        return "awaiting_approval"
    if latest["state"] == "approved":
        return "approved"
    if latest["state"] == "rejected":
        return "rejected"
    run = latest["execution"]
    if run is None:
        return "ready_to_send"
    return {"succeeded": "sent", "rejected": "refused"}.get(run["outcome"], "no_answer")


def last_sent_on(props: list[dict], tz: str) -> date | None:
    days = [local_date(p["execution"]["completed_at"], tz) for p in props if _sent(p) and p["execution"]["completed_at"]]
    return max(days) if days else None


_DSEL = """SELECT d.*, u.display_name AS created_by_name, x.display_name AS discarded_by_name, c.address, contact_state(c.id) AS contact_state
           FROM reminder_draft d JOIN app_user u ON u.id = d.created_by LEFT JOIN app_user x ON x.id = d.discarded_by
           JOIN party_contact c ON c.id = d.contact_id
           WHERE d.business_id = :b"""


def draft_rows(db: Session, b: UUID, draft_id=None, obligation_id: str | None = None, lock: bool = False) -> list:
    q = _DSEL + (" AND d.id = :d" if draft_id else "") + (" AND d.obligation_id = CAST(:o AS uuid)" if obligation_id else "") + " ORDER BY d.created_at DESC"
    return db.execute(text(q + (" FOR UPDATE OF d" if lock else "")), {"b": b, "d": draft_id, "o": obligation_id}).mappings().all()


def draft_out(d, props: list[dict]) -> dict:
    mine = [p for p in props if p["draft_id"] == str(d["id"])]
    latest = mine[-1] if mine else None
    st = draft_state(d["discarded_at"] is not None, latest)
    f = d["facts"]
    return {"id": str(d["id"]), "state": st, "rules_version": d["rules_version"],
            "obligation_id": str(d["obligation_id"]), "document_number": f["document_number"], "customer": f["customer"], "party_id": str(d["party_id"]),
            "currency": f["currency"], "outstanding_minor": f["outstanding_minor"], "amount_minor": f["amount_minor"], "due_date": f["due_date"],
            "issue_date": f["issue_date"], "facts": f, "facts_sha256": d["facts_sha256"],
            "contact": {"id": str(d["contact_id"]), "channel": d["channel"], "masked": mask(d["channel"], d["address"]), "state": d["contact_state"]},
            "note": d["note"], "message": d["message"],
            "created_by": d["created_by_name"], "created_role": d["created_role"], "created_at": iso(d["created_at"]),
            "discarded": {"at": iso(d["discarded_at"]), "by": d["discarded_by_name"], "reason": d["discard_reason"]} if d["discarded_at"] else None,
            # The approval link and the sandbox outcome, newest last. A sent reminder never changes the invoice.
            "proposals": mine, "proposal": latest}


def drafts(db: Session, b: UUID, now: datetime, draft_id=None) -> list[dict]:
    rows = draft_rows(db, b, draft_id)
    props = reminder_proposals(db, b, now)
    return [draft_out(d, props) for d in rows]


def problems(db: Session, b: UUID, today: date, tz: str, o: dict, contact, props: list[dict], draft_id: str | None = None) -> list[str]:
    """Why a reminder for this invoice cannot go ahead now (an empty list means it can)."""
    out = []
    doc = o["document_number"]
    if o["status"] not in ("open", "partially_settled"):
        out.append(f"{doc} is {o['status'].replace('_', ' ')}; nothing is outstanding.")
    elif o["disputed"]:
        out.append(f"{doc} is disputed; resolve the dispute before sending a reminder.")
    elif not o["overdue"]:
        out.append(f"{doc} is not overdue (due {dmy(o['due_date'])}); only an overdue invoice gets a reminder.")
    if contact is None:
        out.append("Choose a contact.")
    elif str(contact["party_id"]) != o["party"]["id"]:
        out.append(f"That contact belongs to {contact['party_name']}, not to {o['party']['name']}.")
    elif contact["state"] != "verified":
        what = {"unverified": "is not verified yet", "rejected": "was rejected", "retired": "was retired"}[contact["state"]]
        out.append(f"The contact {mask(contact['channel'], contact['address'])} {what}; a reminder goes only to a verified contact.")
    others = [p for p in props if p["draft_id"] != draft_id]
    if any(p["state"] in ("pending", "approved") or _unfinished(p) for p in others):
        out.append(f"A reminder for {doc} is already awaiting approval or sending; finish or withdraw it first.")
    sent = last_sent_on(props, tz)
    if sent and today < sent + timedelta(days=REMIND_INTERVAL_DAYS):
        out.append(f"A reminder for {doc} was sent in the sandbox on {dmy(sent)}; the next one can go from {dmy(sent + timedelta(days=REMIND_INTERVAL_DAYS))}.")
    return out


def reminder_payload(db: Session, b: UUID, today: date, tz: str, draft, o: dict, self_id=None, release: bool = False) -> dict:
    """The exact `invoice_reminder` payload for a draft, checked against today's records. Raises DraftProblem."""
    doc = o["document_number"]
    if draft["discarded_at"] is not None:
        raise DraftProblem("This draft was discarded; write a new draft.")
    current = facts_of(o)
    if sha256(current) != draft["facts_sha256"]:
        changed = [k for k in current if current[k] != draft["facts"].get(k)]
        raise DraftProblem(f"{doc} changed since this draft was written ({', '.join(changed)}); write a new draft.")
    props = [p for p in reminder_proposals(db, b, utc_now(), o["id"]) if p["id"] != (str(self_id) if self_id else None)]
    if not release and any(p["state"] == "rejected" for p in props if p["draft_id"] == str(draft["id"])):
        raise DraftProblem("The owner rejected this draft; write a new draft.")
    contact = contact_row(db, b, draft["contact_id"])
    found = problems(db, b, today, tz, o, contact, props)
    if found:
        raise DraftProblem(found[0])
    return {"draft_id": str(draft["id"]), "obligation_id": o["id"], "document_number": doc, "customer": o["party"]["name"],
            "outstanding_minor": o["outstanding_minor"], "currency": o["currency"], "due_date": o["due_date"],
            "channel": draft["channel"], "contact_id": str(draft["contact_id"]), "recipient": mask(contact["channel"], contact["address"]),
            "message": draft["message"], "facts_sha256": draft["facts_sha256"], "delivery": "sandbox"}


# --- ageing and suggestions -------------------------------------------------------------------------------------


def overview(db: Session, b: UUID, today: date, tz: str) -> dict:
    now = utc_now()
    items = [o for o in list_obligations(db, b, today, direction="receivable") if o["status"] in ("open", "partially_settled")]
    props = reminder_proposals(db, b, now)
    by_ob: dict = defaultdict(list)
    for p in props:
        by_ob[p["obligation_id"]].append(p)
    active_drafts = {}
    for d in draft_rows(db, b):
        out = draft_out(d, props)
        if out["state"] in ACTIVE and out["obligation_id"] not in active_drafts:
            active_drafts[out["obligation_id"]] = out
    verified, unverified = defaultdict(int), defaultdict(int)
    for c in db.execute(text("SELECT party_id, contact_state(id) AS state FROM party_contact WHERE business_id = :b"), {"b": b}).mappings():
        if c["state"] == "verified":
            verified[str(c["party_id"])] += 1
        elif c["state"] == "unverified":
            unverified[str(c["party_id"])] += 1

    totals: dict = defaultdict(lambda: {**{k: 0 for k in BUCKETS}, "disputed": 0, "total_outstanding": 0, "overdue_minor": 0,
                                        "invoices_overdue": 0, "customers_overdue": set()})
    customers: dict = {}
    invoices = []
    for o in items:
        bk, cur, amt, pid = bucket(o), o["currency"], o["outstanding_minor"], o["party"]["id"]
        t = totals[cur]
        t[bk] += amt
        t["total_outstanding"] += amt
        c = customers.setdefault((pid, cur), {"party_id": pid, "name": o["party"]["name"], "currency": cur, **{k: 0 for k in BUCKETS},
                                              "disputed": 0, "total_outstanding": 0, "overdue_minor": 0, "oldest_days": 0,
                                              "verified_contacts": verified[pid], "unverified_contacts": unverified[pid], "invoices": []})
        c[bk] += amt
        c["total_outstanding"] += amt
        if bk not in ("disputed", "not_due"):
            t["overdue_minor"] += amt
            t["invoices_overdue"] += 1
            t["customers_overdue"].add(pid)
            c["overdue_minor"] += amt
            c["oldest_days"] = max(c["oldest_days"], o["days_overdue"])
        mine = by_ob[o["id"]]
        sent = last_sent_on(mine, tz)
        reminder: dict = {"last_sent_on": sent.isoformat() if sent else None}
        draft = active_drafts.get(o["id"])
        if bk == "disputed":
            reminder |= {"status": "disputed", "detail": o["dispute_reason"]}
        elif bk == "not_due":
            reminder |= {"status": "not_due"}
        elif draft:
            reminder |= {"status": draft["state"], "draft_id": draft["id"], "proposal_id": draft["proposal"]["id"] if draft["proposal"] else None}
        elif any(p["state"] in ("pending", "approved") or _unfinished(p) for p in mine):
            reminder |= {"status": "in_approval"}
        elif sent and today < sent + timedelta(days=REMIND_INTERVAL_DAYS):
            reminder |= {"status": "recently_sent", "next_on": (sent + timedelta(days=REMIND_INTERVAL_DAYS)).isoformat()}
        elif not verified[pid]:
            reminder |= {"status": "needs_contact" if not unverified[pid] else "contact_unverified"}
        else:
            reminder |= {"status": "ready"}
        row = {k: o[k] for k in ("id", "document_number", "issue_date", "due_date", "amount_minor", "outstanding_minor", "currency",
                                 "days_overdue", "disputed", "dispute_reason", "status")} | {"customer": o["party"], "bucket": bk, "reminder": reminder}
        invoices.append(row)
        c["invoices"].append(row)

    suggestions = []
    for c in customers.values():
        overdue = [i for i in c["invoices"] if i["bucket"] not in ("disputed", "not_due")]
        if not overdue:
            continue
        overdue.sort(key=lambda i: (-i["days_overdue"], -i["outstanding_minor"]))
        ready = [i for i in overdue if i["reminder"]["status"] == "ready"]
        statuses = {i["reminder"]["status"] for i in overdue}
        if ready:
            step = {"action": "draft", "obligation_id": ready[0]["id"], "document_number": ready[0]["document_number"]}
        elif statuses & {"needs_contact", "contact_unverified"}:
            step = {"action": "verify_contact" if "contact_unverified" in statuses else "add_contact"}
        elif statuses & set(ACTIVE + ("in_approval",)):
            step = {"action": "in_progress"}
        else:
            nxt = min((i["reminder"]["next_on"] for i in overdue if i["reminder"].get("next_on")), default=None)
            step = {"action": "wait", "next_on": nxt}
        oldest = c["oldest_days"]
        n = len(overdue)
        suggestions.append({
            "party_id": c["party_id"], "name": c["name"], "currency": c["currency"], "overdue_minor": c["overdue_minor"], "invoices_overdue": n,
            "oldest_days": oldest, "oldest_document": overdue[0]["document_number"],
            "priority": "high" if oldest > 30 else "medium" if oldest > 7 else "low",
            "reason": f"{fmt(c['overdue_minor'], c['currency'])} overdue on {n} invoice{'s' if n > 1 else ''}; oldest {oldest} days ({overdue[0]['document_number']}).",
            "disputed_minor": c["disputed"], "next_step": step})
    rank = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: (rank[s["priority"]], -s["overdue_minor"], s["name"].lower()))
    excluded = [{"id": i["id"], "document_number": i["document_number"], "customer": i["customer"]["name"], "outstanding_minor": i["outstanding_minor"],
                 "currency": i["currency"], "reason": f"Disputed{': ' + i['dispute_reason'] if i['dispute_reason'] else ''}. Resolve it before sending a reminder."}
                for i in invoices if i["bucket"] == "disputed"]
    invoices.sort(key=lambda i: (i["bucket"] == "not_due", -i["days_overdue"], i["document_number"]))
    return {
        "as_of": today.isoformat(), "rules_version": RULES_VERSION, "remind_interval_days": REMIND_INTERVAL_DAYS,
        "buckets": [{"code": k, "label": BUCKET_LABELS[k]} for k in BUCKETS],
        "totals": [{"currency": cur, **{k: v for k, v in t.items() if k != "customers_overdue"}, "customers_overdue": len(t["customers_overdue"])}
                   for cur, t in sorted(totals.items())],
        "customers": sorted(({k: v for k, v in c.items() if k != "invoices"} for c in customers.values()),
                            key=lambda c: (-c["overdue_minor"], -c["total_outstanding"], c["name"].lower())),
        "suggestions": suggestions, "excluded": excluded, "invoices": invoices,
        "drafts_active": len(active_drafts),
    }

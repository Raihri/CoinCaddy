"""Fraud Monitor service: load payment records, run fraud-rules-v1, store flags and holds, and work out flag state.

Nothing here changes a ledger transaction, an invoice or bill, a payment, or a proposal. Flags, holds, and reviews are
their own append-only rows, so the original records are always preserved as they were.
"""

import copy
import hashlib
import hmac
import re
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts.business_time import utc_now
from app.modules.approvals.policy import state as proposal_state
from app.modules.audit.redaction import mask_digits
from app.modules.fraud import rules
from app.modules.outlook.engine import compute
from app.modules.outlook.service import canonical, freeze_inputs, sha256
from app.modules.readiness.engine import fmt
from app.modules.records.service import business_today

_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){7,}\d(?!\d)")
_ISO_DATE = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
OPEN_PROPOSAL_STATES = ("pending", "approved", "executable")
UNRESOLVED = ("open", "deferred")


def masked(value: str | None) -> str | None:
    """Mask account-like digit runs in imported text (payee, reference) before it is copied into a flag; dates are kept."""
    if not value:
        return value
    parts = _ISO_DATE.split(value)
    dates = _ISO_DATE.findall(value)
    out = _DIGIT_RUN.sub(lambda m: mask_digits(m.group(0)), parts[0])
    for d, part in zip(dates, parts[1:]):
        out += d + _DIGIT_RUN.sub(lambda m: mask_digits(m.group(0)), part)
    return out


def destination_hmac(business_id: UUID | str, kind: str, digits: str) -> str:
    """Keyed hash of a destination number: equal numbers match, but the number cannot be recovered or guessed from the row."""
    key = get_settings().destination_hash_key.encode()
    return hmac.new(key, f"{business_id}:{kind}:{digits}".encode(), hashlib.sha256).hexdigest()


def _tz(db: Session, b: UUID) -> str:
    return db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": b}).scalar_one()


# --- loading -----------------------------------------------------------------------------------------------------


def open_payment_proposals(db: Session, b: UUID, tz: str, now: datetime) -> list[dict]:
    rows = db.execute(text(
        """SELECT p.id, p.payload, p.created_at, p.expires_at, o.party_id, array_remove(array_agg(d.event), NULL) AS events
           FROM action_proposal p
           LEFT JOIN obligation o ON o.business_id = p.business_id AND o.id = CAST(p.payload ->> 'obligation_id' AS uuid)
           LEFT JOIN action_decision d ON d.proposal_id = p.id
           WHERE p.business_id = :b AND p.kind = 'supplier_payment'
           GROUP BY p.id, o.party_id"""), {"b": b}).mappings().all()
    out = []
    for r in rows:
        st = proposal_state(r, [{"event": e} for e in r["events"]], now)
        if st not in OPEN_PROPOSAL_STATES:
            continue
        p = r["payload"]
        out.append({"type": "action_proposal", "id": str(r["id"]), "date": r["created_at"].astimezone(ZoneInfo(tz)).date().isoformat(),
                    "payee": p["supplier"], "payee_key": rules.payee_key(p["supplier"]), "amount_minor": p["amount_minor"], "currency": p["currency"],
                    "reference": p["document_number"], "account_id": p["from_account_id"], "account": p["from_account"],
                    "status": st, "party_id": str(r["party_id"]) if r["party_id"] else None, "obligation_id": p["obligation_id"]})
    return out


def load_inputs(db: Session, b: UUID, as_of: date, tz: str) -> dict:
    since = as_of - timedelta(days=rules.HISTORY_DAYS + rules.SETTLEMENT_MATCH_DAYS)
    not_transfer = "NOT EXISTS (SELECT 1 FROM own_transfer t WHERE t.voided_at IS NULL AND (t.out_txn_id = lt.id OR t.in_txn_id = lt.id))"
    ledger = db.execute(text(
        f"""SELECT lt.id, lt.business_date, lt.amount_minor, lt.currency, lt.counterparty, lt.reference, lt.account_id, ca.label
            FROM ledger_transaction lt JOIN cash_account ca ON ca.id = lt.account_id
            WHERE lt.business_id = :b AND lt.amount_minor < 0 AND lt.business_date >= :since AND {not_transfer}"""),
        {"b": b, "since": since}).mappings().all()
    items = []
    for r in ledger:
        payee = masked(r["counterparty"])
        items.append({"type": "ledger_transaction", "id": str(r["id"]), "date": r["business_date"].isoformat(), "payee": payee,
                      "payee_key": rules.payee_key(payee), "amount_minor": -r["amount_minor"], "currency": r["currency"],
                      "reference": masked(r["reference"]), "account_id": str(r["account_id"]), "account": r["label"], "status": "posted", "party_id": None})
    items += open_payment_proposals(db, b, tz, utc_now())
    parties = [{"id": str(r["id"]), "name": r["name"], "first_seen": r["created_at"].astimezone(ZoneInfo(tz)).date().isoformat()}
               for r in db.execute(text("SELECT id, name, created_at FROM party WHERE business_id = :b"), {"b": b}).mappings()]
    destinations = [{"id": str(r["id"]), "party_id": str(r["party_id"]), "party": r["name"], "kind": r["kind"], "institution": r["institution"],
                     "last4": r["last4"], "number_hmac": r["number_hmac"], "change_source": r["change_source"], "recorded_by": str(r["recorded_by"]),
                     "date": r["created_at"].astimezone(ZoneInfo(tz)).date().isoformat()}
                    for r in db.execute(text("""SELECT d.*, p.name FROM payee_destination d JOIN party p ON p.id = d.party_id
                                                WHERE d.business_id = :b ORDER BY d.created_at, d.id"""), {"b": b}).mappings()]
    allocations = [{"id": str(r["id"]), "obligation_id": str(r["obligation_id"]), "direction": r["direction"], "document_number": r["document_number"],
                    "party": r["name"], "amount_minor": r["amount_minor"], "currency": r["currency"], "date": r["allocation_date"].isoformat()}
                   for r in db.execute(text(
                       """SELECT a.id, a.obligation_id, o.direction, o.document_number, p.name, a.amount_minor, a.currency, a.allocation_date
                          FROM allocation a JOIN obligation o ON o.id = a.obligation_id JOIN party p ON p.id = o.party_id
                          WHERE a.business_id = :b AND a.kind = 'payment' AND a.ledger_txn_id IS NULL AND o.cancelled_at IS NULL
                            AND a.allocation_date >= :since AND NOT EXISTS (SELECT 1 FROM allocation r WHERE r.reverses_id = a.id)"""),
                       {"b": b, "since": as_of - timedelta(days=rules.HISTORY_DAYS)}).mappings()]
    movements = [{"id": str(r["id"]), "amount_minor": r["amount_minor"], "currency": r["currency"], "date": r["business_date"].isoformat()}
                 for r in db.execute(text(
                     f"""SELECT lt.id, lt.amount_minor, lt.currency, lt.business_date FROM ledger_transaction lt
                         WHERE lt.business_id = :b AND lt.business_date >= :since AND {not_transfer}
                           AND NOT EXISTS (SELECT 1 FROM allocation a WHERE a.ledger_txn_id = lt.id)"""), {"b": b, "since": since}).mappings()]
    return {"items": items, "parties": parties, "destinations": destinations, "allocations": allocations, "movements": movements}


# --- running the rules ---------------------------------------------------------------------------------------------


def run_scan(db: Session, b: UUID, user_id: UUID, trigger: str, proposal_id: UUID | None = None) -> dict:
    """Evaluate every rule and store what is new. Repeated runs add no duplicate flag; a new open payment proposal that
    an uncleared flag covers is added to that flag's holds. Runs in the caller's transaction."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"fraud:{b}"})
    tz = _tz(db, b)
    as_of = business_today(tz)
    data = load_inputs(db, b, as_of, tz)
    found = rules.evaluate(as_of=as_of, **data)
    existing = {} if not found else {r["dedup_key"]: r for r in db.execute(text(
        """SELECT f.id, f.dedup_key, fraud_flag_cleared(f.id) AS cleared,
                  coalesce(array_agg(h.proposal_id) FILTER (WHERE h.proposal_id IS NOT NULL), '{}') AS held
           FROM fraud_flag f LEFT JOIN fraud_flag_hold h ON h.flag_id = f.id
           WHERE f.business_id = :b AND f.dedup_key = ANY(:k) GROUP BY f.id"""),
        {"b": b, "k": [c["dedup_key"] for c in found]}).mappings()}
    new = [c for c in found if c["dedup_key"] not in existing]
    extra_holds = [(existing[c["dedup_key"]]["id"], pid) for c in found if c["dedup_key"] in existing and not existing[c["dedup_key"]]["cleared"]
                   for pid in c["hold_proposal_ids"] if UUID(pid) not in set(existing[c["dedup_key"]]["held"])]
    holds_added = len(extra_holds) + sum(len(c["hold_proposal_ids"]) for c in new)
    scan_id = db.execute(text(
        """INSERT INTO fraud_scan (business_id, rules_version, as_of, trigger, proposal_id, items_checked, flags_created, flags_existing, holds_added, created_by)
           VALUES (:b, :v, :d, :t, :p, :n, :c, :e, :h, :u) RETURNING id"""),
        {"b": b, "v": rules.RULES_VERSION, "d": as_of, "t": trigger, "p": proposal_id, "n": len(data["items"]) + len(data["allocations"]),
         "c": len(new), "e": len(found) - len(new), "h": holds_added, "u": user_id}).scalar_one()
    created = []
    for c in new:
        fid = db.execute(text(
            """INSERT INTO fraud_flag (business_id, rule, rules_version, dedup_key, severity, title, reason, currency, amount_minor,
                                       subject_refs, compared_refs, evidence, party_id, destination_id, first_scan_id)
               VALUES (:b, :r, :v, :k, :s, :t, :reason, :cur, :amt, CAST(:sr AS jsonb), CAST(:cr AS jsonb), CAST(:ev AS jsonb), :party, :dest, :scan)
               ON CONFLICT (business_id, dedup_key) DO NOTHING RETURNING id"""),
            {"b": b, "r": c["rule"], "v": rules.RULES_VERSION, "k": c["dedup_key"], "s": c["severity"], "t": c["title"], "reason": c["reason"],
             "cur": c["currency"], "amt": c["amount_minor"], "sr": canonical(c["subject_refs"]), "cr": canonical(c["compared_refs"]),
             "ev": canonical(c["evidence"]), "party": c["party_id"], "dest": c["destination_id"], "scan": scan_id}).scalar_one_or_none()
        if fid is not None:
            created.append(fid)
            extra_holds += [(fid, pid) for pid in c["hold_proposal_ids"]]
    for fid, pid in extra_holds:
        db.execute(text("""INSERT INTO fraud_flag_hold (business_id, flag_id, proposal_id, scan_id) VALUES (:b, :f, :p, :s)
                           ON CONFLICT (flag_id, proposal_id) DO NOTHING"""), {"b": b, "f": fid, "p": pid, "s": scan_id})
    return {"scan_id": str(scan_id), "as_of": as_of.isoformat(), "rules_version": rules.RULES_VERSION, "items_checked": len(data["items"]) + len(data["allocations"]),
            "flags_created": len(created), "flags_existing": len(found) - len(new), "holds_added": holds_added}


# --- state ---------------------------------------------------------------------------------------------------------


def flag_state(reviews: list) -> str:
    decisions = [r["decision"] for r in reviews]
    if "rejected" in decisions:
        return "cleared"
    if "confirmed" in decisions:
        return "confirmed"
    return "deferred" if decisions else "open"


def reviews_for(db: Session, flag_ids: list) -> dict:
    out: dict = {f: [] for f in flag_ids}
    if flag_ids:
        for r in db.execute(text("""SELECT r.*, u.display_name AS reviewer_name FROM fraud_review r JOIN app_user u ON u.id = r.reviewer_id
                                    WHERE r.flag_id = ANY(:ids) ORDER BY r.created_at, r.id"""), {"ids": flag_ids}).mappings():
            out[r["flag_id"]].append(r)
    return out


def holds_for(db: Session, b: UUID, proposal_ids: list) -> dict:
    """Uncleared flags holding each proposal: {proposal_id: [{flag_id, rule, title}]}."""
    out: dict = {p: [] for p in proposal_ids}
    if proposal_ids:
        for r in db.execute(text("""SELECT h.proposal_id, f.id, f.rule, f.title FROM fraud_flag_hold h JOIN fraud_flag f ON f.id = h.flag_id
                                    WHERE h.business_id = :b AND h.proposal_id = ANY(:ids) AND NOT fraud_flag_cleared(f.id)
                                    ORDER BY f.created_at"""), {"b": b, "ids": proposal_ids}).mappings():
            out[r["proposal_id"]].append({"flag_id": str(r["id"]), "rule": r["rule"], "title": r["title"]})
    return out


# --- bounded cash outcomes -----------------------------------------------------------------------------------------


def materiality_threshold(inputs: dict, currency: str) -> int:
    """1% of the reserve in the flag's currency (as the evidence queue uses), else every amount counts as material."""
    r = inputs["reserve"]
    return max(1, r["amount_minor"] // 100) if r["currency"] == currency and r["amount_minor"] else 1


def _scenarios(flag: dict, inputs: dict) -> tuple[dict, dict, list[str]]:
    """Changes for outcome A (the flag is wrong: the payment stands or goes ahead) and outcome B (the flag is right: a posted
    payment is recovered, a proposed one is stopped), plus the assumptions. Each is {"accounts": {id: delta}, "obligations":
    {id: amount no longer owed}}. A proposed payment settles its bill, so the bill's outflow is replaced, never counted twice."""
    a: dict = {"accounts": {}, "obligations": {}}
    b: dict = {"accounts": {}, "obligations": {}}
    notes: list[str] = []
    cur = flag["currency"]
    default = next((x["id"] for x in inputs["accounts"] if x["currency"] == cur and x["usable_minor"] is not None), None)

    def add(scenario: dict, part: str, key: str, value: int) -> None:
        scenario[part][key] = scenario[part].get(key, 0) + value

    posted_compared = any(c.get("type") == "ledger_transaction" for c in flag["evidence"]["compared"])
    for s in flag["evidence"]["subjects"]:
        amt, acc = s.get("amount_minor"), s.get("account_id") or default
        if not amt or s.get("currency") != cur:
            continue
        if s["type"] == "ledger_transaction":
            add(b, "accounts", acc, amt)
            notes.append(f"{fmt(amt, cur)} already left {s.get('account') or 'the account'}; if it was not genuine it is assumed recovered tomorrow (the best case).")
        elif s["type"] == "action_proposal":
            add(a, "accounts", acc, -amt)
            if s.get("obligation_id"):
                add(a, "obligations", s["obligation_id"], amt)
            notes.append(f"If the proposed {fmt(amt, cur)} goes ahead it leaves {s.get('account') or 'the account'} tomorrow and settles {s.get('reference')}, "
                         "so that bill's own outflow is not counted again.")
            if flag["rule"] == "duplicate_payment" and posted_compared and s.get("obligation_id"):
                add(b, "obligations", s["obligation_id"], amt)
                notes.append(f"If it is a duplicate, {s.get('reference')} was already paid by the earlier payment, so it is no longer owed.")
            else:
                notes.append("If it is stopped, the bill stays owed to the genuine payee on its due date.")
        elif s["type"] == "allocation" and s.get("direction") == "payable":
            add(b, "accounts", acc, -amt)
            notes.append(f"If the bill was never paid, {fmt(amt, cur)} is still owed and is assumed paid tomorrow.")
        elif s["type"] == "allocation":
            add(a, "accounts", acc, amt)
            notes.append(f"If the customer paid outside these records, {fmt(amt, cur)} is assumed available tomorrow.")
    return a, b, notes


def _lowest(inputs: dict, change: dict, cur: str) -> dict | None:
    variant = copy.deepcopy(inputs)
    for acc in variant["accounts"]:
        if acc["id"] in change["accounts"]:
            if acc["usable_minor"] is None:
                return None
            acc["usable_minor"] += change["accounts"][acc["id"]]
    if any(k not in {x["id"] for x in variant["accounts"]} for k in change["accounts"]):
        return None
    for o in variant["obligations"]:
        if o["id"] in change["obligations"]:
            o["outstanding_minor"] = max(0, o["outstanding_minor"] - change["obligations"][o["id"]])
    variant["obligations"] = [o for o in variant["obligations"] if o["outstanding_minor"] > 0]
    res = next((c for c in compute(variant)["currencies"] if c["currency"] == cur), None)
    if res is None:
        return None
    breach = res["first_breach"]["expected"]
    return {"lowest_expected": res["lowest"]["expected"], "lowest_cautious": res["lowest"]["cautious"],
            "below_reserve": breach is not None, "first_breach": breach}


def cash_outcomes(db: Session, b: UUID, flag: dict, st: str) -> dict:
    """Two bounded outcomes for a material, unresolved candidate, on today's frozen Cash Outlook inputs (nothing is stored)."""
    if st not in UNRESOLVED:
        return {"available": False, "reason": "Outcomes are shown only while a flag is unresolved."}
    labels = rules.RULES[flag["rule"]]["outcomes"]
    if not flag["amount_minor"]:
        return {"available": False, "reason": "No payment amount is affected yet. Any payment proposed to this payee is held until the flag is cleared."}
    inputs = freeze_inputs(db, b, business_today(_tz(db, b)))
    threshold = materiality_threshold(inputs, flag["currency"])
    if flag["amount_minor"] < threshold:
        return {"available": False, "reason": f"Below the materiality threshold of {fmt(threshold, flag['currency'])} (1% of the reserve), so the cash effect is not calculated.",
                "materiality_threshold_minor": threshold}
    da, db_, notes = _scenarios(flag, inputs)
    first, second = _lowest(inputs, da, flag["currency"]), _lowest(inputs, db_, flag["currency"])
    if first is None or second is None:
        return {"available": False, "reason": f"The affected account has no verified balance or no {flag['currency']} Cash Outlook path, so the outcomes cannot be bounded. Enter a balance anchor in Records."}
    return {"available": True, "as_of": inputs["as_of"], "calc_version": inputs["calc_version"], "inputs_sha256": sha256(inputs),
            "currency": flag["currency"], "reserve_minor": inputs["reserve"]["amount_minor"] if inputs["reserve"]["currency"] == flag["currency"] else None,
            "materiality_threshold_minor": threshold, "assumptions": notes,
            "outcomes": [{"label": labels[0], **first}, {"label": labels[1], **second}],
            "difference_minor": abs(first["lowest_expected"]["amount_minor"] - second["lowest_expected"]["amount_minor"])}

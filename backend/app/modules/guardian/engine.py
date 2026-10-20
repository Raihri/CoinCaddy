"""Receivables Guardian rules, version guardian-v1. Pure: invoice facts in, aging, suggestions, and draft text out.

It reads what the obligations module already computed (outstanding amount, days overdue, disputed) and never
recalculates or changes it. Aging buckets: not yet due, 1-30, 31-60, 61-90, and over 90 days overdue. A disputed
invoice is shown separately and is never suggested, drafted, or proposed: a dispute is a conversation, not a reminder.

An invoice can get a reminder only if it is outstanding, overdue, and undisputed, has no reminder already in approval,
has not had a reminder sent through the sandbox in the last 7 days, and the customer has a VERIFIED contact on the
chosen channel. A customer is suggested when at least one invoice qualifies; suggestions are ordered by the oldest
overdue bucket, then the larger outstanding amount, then name. The draft is built only from the invoice's own facts.
"""

import hashlib
import hmac
import re
from collections import defaultdict
from datetime import date

from app.modules.readiness.engine import fmt

RULES_VERSION = "guardian-v1"
COOLDOWN_DAYS = 7
BUCKETS = ("current", "1-30", "31-60", "61-90", "91+")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    return "1-30" if days_overdue <= 30 else "31-60" if days_overdue <= 60 else "61-90" if days_overdue <= 90 else "91+"


def stage(days_overdue: int) -> int:
    return 1 if days_overdue <= 14 else 2 if days_overdue <= 45 else 3


def age(invoices: list[dict]) -> list[dict]:
    """Per currency: outstanding amount and count in each bucket, with disputed invoices kept apart."""
    out: dict[str, dict] = defaultdict(lambda: {"buckets": {b: {"amount_minor": 0, "count": 0} for b in BUCKETS}, "disputed": {"amount_minor": 0, "count": 0},
                                                "total_minor": 0})
    for i in invoices:
        row = out[i["currency"]]
        row["total_minor"] += i["outstanding_minor"]
        target = row["disputed"] if i["disputed"] else row["buckets"][bucket(i["days_overdue"])]
        target["amount_minor"] += i["outstanding_minor"]
        target["count"] += 1
    return [{"currency": c, **v} for c, v in sorted(out.items())]


def normalize_address(channel: str, raw: str, business_id: str, secret: str) -> tuple[str, str]:
    """Return a masked address and a business-scoped keyed digest; never retain the full address."""
    def digest(value: str) -> str:
        payload = f"guardian-contact:{business_id}:{channel}:{value}".encode()
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    raw = raw.strip()
    if channel == "email":
        if not EMAIL.match(raw) or len(raw) > 120:
            raise ValueError("Enter a valid e-mail address.")
        norm = raw.lower()
        local, domain = norm.split("@", 1)
        return f"{local[0]}***@{domain}", digest(norm)
    digits = re.sub(r"[\s().-]", "", raw)
    if not re.fullmatch(r"\+?\d{7,15}", digits):
        raise ValueError("Enter a valid phone number with 7 to 15 digits.")
    norm = digits.lstrip("+")
    return f"••••{norm[-4:]}", digest(norm)


def blockers(inv: dict, *, open_proposal: bool, last_sent: date | None, today: date) -> list[str]:
    """Why this invoice cannot get a reminder right now (empty = it can, once a verified contact is chosen)."""
    out = []
    if inv["status"] not in ("open", "partially_settled"):
        out.append(f"{inv['document_number']} is {inv['status'].replace('_', ' ')}; nothing is outstanding.")
    elif inv["disputed"]:
        out.append(f"{inv['document_number']} is disputed; resolve the dispute before any reminder.")
    elif inv["days_overdue"] <= 0:
        out.append(f"{inv['document_number']} is not overdue (due {inv['due_date']}).")
    if open_proposal:
        out.append("A reminder for this invoice is already waiting for a decision or release.")
    if last_sent is not None and (today - last_sent).days < COOLDOWN_DAYS:
        out.append(f"A reminder was sent on {last_sent.isoformat()}; wait until {date.fromordinal(last_sent.toordinal() + COOLDOWN_DAYS).isoformat()} before another.")
    return out


def facts_of(inv: dict, contact_id: str, channel: str, as_of: date) -> dict:
    return {"obligation_id": inv["id"], "party_id": inv["party"]["id"], "document_number": inv["document_number"], "customer": inv["party"]["name"],
            "outstanding_minor": inv["outstanding_minor"], "currency": inv["currency"], "due_date": inv["due_date"], "days_overdue": inv["days_overdue"],
            "as_of": as_of.isoformat(), "stage": stage(inv["days_overdue"]), "contact_id": contact_id, "channel": channel, "rules_version": RULES_VERSION}


def draft_message(facts: dict, business_name: str, note: str | None = None) -> str:
    amount, due, n, doc, who = fmt(facts["outstanding_minor"], facts["currency"]), facts["due_date"], facts["days_overdue"], facts["document_number"], facts["customer"]
    days = f"{n} day{'s' if n != 1 else ''}"
    body = {
        1: f"Dear {who}, a friendly reminder that invoice {doc} for {amount} was due on {due} and is now {days} overdue. "
           f"If you have already paid, please let us know. Thank you.",
        2: f"Dear {who}, invoice {doc} for {amount} was due on {due} and is now {days} overdue. "
           f"Please arrange payment as soon as possible, or contact us if there is a problem.",
        3: f"Dear {who}, invoice {doc} for {amount} is still unpaid {days} after its due date of {due}. "
           f"Please contact us this week to arrange payment.",
    }[facts["stage"]]
    msg = f"{body} {note.strip()}" if note and note.strip() else body
    return f"{msg} - {business_name}"[:500]


def suggest(customers: list[dict]) -> list[dict]:
    """customers: [{party, invoices:[{..., eligible: bool, bucket, outstanding_minor, days_overdue}], ...}] -> ranked suggestions."""
    out = []
    for c in customers:
        ok = [i for i in c["invoices"] if i["eligible"]]
        if not ok:
            continue
        oldest = max(i["days_overdue"] for i in ok)
        total = sum(i["outstanding_minor"] for i in ok)
        out.append({"party_id": c["party"]["id"], "customer": c["party"]["name"], "invoice_count": len(ok), "oldest_days_overdue": oldest,
                    "oldest_bucket": bucket(oldest), "outstanding": [{"currency": cur, "amount_minor": sum(i["outstanding_minor"] for i in ok if i["currency"] == cur)}
                                                                    for cur in sorted({i["currency"] for i in ok})],
                    "reason": f"{len(ok)} overdue invoice{'s' if len(ok) != 1 else ''}, the oldest {oldest} days overdue, "
                              + ", ".join(fmt(sum(i['outstanding_minor'] for i in ok if i['currency'] == cur), cur) for cur in sorted({i['currency'] for i in ok})) + " outstanding.",
                    "_key": (-BUCKETS.index(bucket(oldest)), -total)})
    out.sort(key=lambda s: (s["_key"], s["customer"].lower()))
    for n, s in enumerate(out, 1):
        s["rank"] = n
        del s["_key"]
    return out

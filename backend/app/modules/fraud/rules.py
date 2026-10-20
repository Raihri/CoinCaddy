"""Transaction Fraud Monitor rules, version fraud-rules-v1. Pure: plain records in, suspected-activity candidates out.

A candidate is suspected activity for a person to review, never a fraud verdict. Every candidate names the records
it compared (subject = the record under suspicion, compared = what it was compared with) and the rule's reason.

Payment items are posted ledger outflows (own-account transfers excluded) and open sandbox supplier-payment
proposals. Rules:

duplicate_payment        a payment with the same currency and amount as an earlier one, within DUPLICATE_DAYS, to the
                         same payee or with the same reference. A proposal is always the subject (it can still be held).
unusual_payee_amount     a payment at least UNUSUAL_MULTIPLE times the median of the payee's earlier posted payments
                         (at least UNUSUAL_MIN_HISTORY of them within HISTORY_DAYS; the most recent 10 are used).
split_near_limit         two or more payments to one payee within SPLIT_DAYS, each between SPLIT_NEAR_PERCENT% of the
                         single-payment review limit and the limit, that together reach the limit.
lookalike_payee          a payee name that is not the same as an established payee's but reads like it (the same after
                         digit/letter look-alikes are folded, or at least LOOKALIKE_SIMILARITY similar). Payments to the
                         newer name are the subject.
destination_change       a payee's bank or wallet destination differs from the one recorded before it. Open payment
                         proposals to that payee are held until the change is independently verified.
paid_without_settlement  a payment recorded on an invoice or bill without a linked ledger movement, at least
                         SETTLEMENT_LAG_DAYS old, and no unlinked ledger movement of the same amount within
                         SETTLEMENT_MATCH_DAYS.
"""

import hashlib
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import date, timedelta

from app.modules.readiness.engine import fmt

RULES_VERSION = "fraud-rules-v1"
HISTORY_DAYS = 180
DUPLICATE_DAYS = 14
UNUSUAL_MULTIPLE = 3
UNUSUAL_MIN_HISTORY = 3
SPLIT_DAYS = 3
SPLIT_NEAR_PERCENT = 60
# Single-payment review limit per currency (minor units). Currencies without a limit skip the split rule.
SPLIT_LIMITS = {"BDT": 5_000_000}
LOOKALIKE_SIMILARITY_PERCENT = 85
LOOKALIKE_MIN_LENGTH = 6
SETTLEMENT_LAG_DAYS = 3
SETTLEMENT_MATCH_DAYS = 7

RULES: dict[str, dict] = {
    "duplicate_payment": {"title": "Possible duplicate payment", "severity": "high",
                          "outcomes": ("If genuine", "If duplicate"),
                          "describe": f"Same currency and amount as an earlier payment within {DUPLICATE_DAYS} days, to the same payee or with the same reference."},
    "unusual_payee_amount": {"title": "Unusual amount for this payee", "severity": "medium",
                             "outcomes": ("If genuine", "If not genuine"),
                             "describe": f"At least {UNUSUAL_MULTIPLE}x the median of the payee's earlier posted payments (needs {UNUSUAL_MIN_HISTORY} or more in {HISTORY_DAYS} days)."},
    "split_near_limit": {"title": "Split payments near the review limit", "severity": "medium",
                         "outcomes": ("If genuine", "If not genuine"),
                         "describe": f"Two or more payments to one payee within {SPLIT_DAYS} days, each {SPLIT_NEAR_PERCENT}-100% of the single-payment review limit, together at or over it."},
    "lookalike_payee": {"title": "Payee name looks like another payee", "severity": "medium",
                        "outcomes": ("If a real, separate payee", "If an impostor"),
                        "describe": f"A newer payee name that reads like an established one (look-alike characters, or {LOOKALIKE_SIMILARITY_PERCENT}% or more similar)."},
    "destination_change": {"title": "Payee destination details changed", "severity": "high",
                           "outcomes": ("If the change is genuine", "If the change is fraudulent"),
                           "describe": "A payee's bank or wallet destination differs from the one recorded before. Needs independent verification."},
    "paid_without_settlement": {"title": "Marked paid without a settlement", "severity": "medium",
                                "outcomes": ("If settled outside these records", "If never settled"),
                                "describe": f"A payment recorded on an invoice or bill with no linked ledger movement after {SETTLEMENT_LAG_DAYS} days, and no matching movement within {SETTLEMENT_MATCH_DAYS} days."},
}

_SUFFIXES = {"ltd", "limited", "co", "company", "inc", "pvt", "plc", "llc", "corp", "corporation", "the"}
_FOLD = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "|": "l", "!": "i"})


def payee_key(name: str | None) -> str | None:
    """Comparable payee name: case, punctuation, spacing, and legal suffixes (Ltd, Co...) are ignored."""
    if not name:
        return None
    s = unicodedata.normalize("NFKC", name).casefold().replace("&", " and ")
    tokens = [t for t in re.sub(r"[^\w@$|!]+", " ", s).split() if t not in _SUFFIXES]
    return " ".join(tokens) or None


def skeleton(key: str) -> str:
    """The name with look-alike characters folded (0->o, 1->l, rn->m, vv->w) and spaces removed."""
    return key.translate(_FOLD).replace(" ", "").replace("rn", "m").replace("vv", "w")


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similarity_percent(a: str, b: str) -> int:
    a, b = a.replace(" ", ""), b.replace(" ", "")
    longest = max(len(a), len(b)) or 1
    return (100 * (longest - _levenshtein(a, b))) // longest


def lookalike(a: str, b: str) -> str | None:
    """'characters' when the names differ only by look-alike characters, 'spelling' when nearly the same, else None."""
    if a == b:
        return None
    if skeleton(a) == skeleton(b):
        return "characters"
    if min(len(a.replace(" ", "")), len(b.replace(" ", ""))) >= LOOKALIKE_MIN_LENGTH and similarity_percent(a, b) >= LOOKALIKE_SIMILARITY_PERCENT:
        return "spelling"
    return None


def _ref(item: dict) -> dict:
    return {"type": item["type"], "id": item["id"]}


def _snap(item: dict) -> dict:
    keys = ("type", "id", "date", "payee", "amount_minor", "currency", "reference", "account_id", "account", "status", "obligation_id")
    return {k: item.get(k) for k in keys}


def _dd(a: dict, b: dict) -> int:
    return abs((date.fromisoformat(a["date"]) - date.fromisoformat(b["date"])).days)


def _candidate(rule: str, key: str, reason: str, currency: str, amount: int, subjects: list[dict], compared: list[dict],
               evidence: dict | None = None, severity: str | None = None, party_id: str | None = None, destination_id: str | None = None,
               hold: list[str] | None = None) -> dict:
    return {"rule": rule, "dedup_key": key, "severity": severity or RULES[rule]["severity"], "title": RULES[rule]["title"], "reason": reason,
            "currency": currency, "amount_minor": amount, "subject_refs": [_ref(s) for s in subjects], "compared_refs": [_ref(c) for c in compared],
            "evidence": {"subjects": [_snap(s) if s["type"] in ("ledger_transaction", "action_proposal") else s for s in subjects],
                         "compared": [_snap(c) if c["type"] in ("ledger_transaction", "action_proposal") else c for c in compared]} | (evidence or {}),
            "party_id": party_id, "destination_id": destination_id,
            "hold_proposal_ids": sorted(set(hold if hold is not None else [s["id"] for s in subjects if s["type"] == "action_proposal"]))}


def _label(item: dict) -> str:
    what = "proposed payment" if item["type"] == "action_proposal" else "payment"
    return f"the {what} of {fmt(item['amount_minor'], item['currency'])} on {item['date']}"


# --- rules -------------------------------------------------------------------------------------------------------


def duplicate_payments(items: list[dict]) -> list[dict]:
    out = []
    ordered = sorted(items, key=lambda i: (i["date"], i["type"] == "action_proposal", i["id"]))
    for idx, b in enumerate(ordered):
        best = None
        for a in ordered[:idx]:
            if a["currency"] != b["currency"] or a["amount_minor"] != b["amount_minor"] or _dd(a, b) > DUPLICATE_DAYS:
                continue
            same_payee = a["payee_key"] is not None and a["payee_key"] == b["payee_key"]
            same_ref = bool(a["reference"]) and a["reference"].casefold() == (b["reference"] or "").casefold()
            if same_payee or same_ref:
                best = (a, same_payee, same_ref)  # the nearest earlier match wins, so three copies give two pairs
        if best is None:
            continue
        a, same_payee, same_ref = best
        subject, other = (a, b) if a["type"] == "action_proposal" and b["type"] == "ledger_transaction" else (b, a)
        why = " and ".join(x for x in ("the same payee" if same_payee else "", "the same reference" if same_ref else "") if x)
        payee = subject["payee"] or other["payee"] or "an unnamed payee"
        reason = (f"{_label(subject).capitalize()} to {payee} has the same amount and currency as {_label(other)}, with {why}, "
                  f"{_dd(a, b)} day(s) apart. It may pay the same obligation twice.")
        out.append(_candidate("duplicate_payment", f"duplicate_payment:{':'.join(sorted([a['id'], b['id']]))}", reason, subject["currency"],
                              subject["amount_minor"], [subject], [other], {"days_apart": _dd(a, b), "same_payee": same_payee, "same_reference": same_ref}))
    return out


def unusual_amounts(items: list[dict]) -> list[dict]:
    out = []
    posted = [i for i in items if i["type"] == "ledger_transaction" and i["payee_key"]]
    for b in items:
        if not b["payee_key"]:
            continue
        bd = date.fromisoformat(b["date"])
        hist = sorted((i for i in posted if i["id"] != b["id"] and i["payee_key"] == b["payee_key"] and i["currency"] == b["currency"]
                       and bd - timedelta(days=HISTORY_DAYS) <= date.fromisoformat(i["date"]) < bd), key=lambda i: (i["date"], i["id"]))[-10:]
        if len(hist) < UNUSUAL_MIN_HISTORY:
            continue
        median = statistics.median_low(i["amount_minor"] for i in hist)
        if b["amount_minor"] >= UNUSUAL_MULTIPLE * median:
            times = b["amount_minor"] // median if median else 0
            reason = (f"{_label(b).capitalize()} to {b['payee']} is about {times}x the median of {len(hist)} earlier payments "
                      f"to this payee ({fmt(median, b['currency'])}).")
            out.append(_candidate("unusual_payee_amount", f"unusual_payee_amount:{b['id']}", reason, b["currency"], b["amount_minor"], [b], hist,
                                  {"median_minor": median, "history_count": len(hist), "multiple_threshold": UNUSUAL_MULTIPLE}))
    return out


def split_payments(items: list[dict]) -> list[dict]:
    out, emitted = [], []
    groups = defaultdict(list)
    for i in items:
        limit = SPLIT_LIMITS.get(i["currency"])
        if i["payee_key"] and limit and i["amount_minor"] * 100 >= SPLIT_NEAR_PERCENT * limit and i["amount_minor"] < limit:
            groups[(i["payee_key"], i["currency"])].append(i)
    for (_, cur), group in groups.items():
        limit = SPLIT_LIMITS[cur]
        group.sort(key=lambda i: (i["date"], i["id"]))
        for start, first in enumerate(group):
            end = date.fromisoformat(first["date"]) + timedelta(days=SPLIT_DAYS)
            cluster = [i for i in group[start:] if date.fromisoformat(i["date"]) <= end]
            total = sum(i["amount_minor"] for i in cluster)
            ids = {i["id"] for i in cluster}
            if len(cluster) < 2 or total < limit or any(ids <= e for e in emitted):
                continue
            emitted.append(ids)
            reason = (f"{len(cluster)} payments to {first['payee']} between {cluster[0]['date']} and {cluster[-1]['date']} are each just under the "
                      f"single-payment review limit of {fmt(limit, cur)} but together come to {fmt(total, cur)}.")
            out.append(_candidate("split_near_limit", f"split_near_limit:{hashlib.sha256(':'.join(sorted(ids)).encode()).hexdigest()}", reason, cur, total, cluster, [],
                                  {"limit_minor": limit, "near_percent": SPLIT_NEAR_PERCENT, "window_days": SPLIT_DAYS}))
    return out


def lookalike_payees(items: list[dict], parties: list[dict]) -> list[dict]:
    """parties: [{id, name, first_seen}] from invoices and bills."""
    names: dict[str, dict] = {}
    for p in parties:
        k = payee_key(p["name"])
        if k:
            n = names.setdefault(k, {"key": k, "name": p["name"], "first_seen": p["first_seen"], "items": [], "party_id": p["id"]})
            n["first_seen"] = min(n["first_seen"], p["first_seen"])
    for i in items:
        if i["payee_key"]:
            n = names.setdefault(i["payee_key"], {"key": i["payee_key"], "name": i["payee"], "first_seen": i["date"], "items": [], "party_id": None})
            n["first_seen"] = min(n["first_seen"], i["date"])
            n["items"].append(i)
    out = []
    ordered = sorted(names.values(), key=lambda n: (n["first_seen"], n["key"]))
    for idx, newer in enumerate(ordered):
        if not newer["items"]:
            continue  # a name nothing has been paid to puts no cash at risk
        for est in ordered[:idx]:
            how = lookalike(est["key"], newer["key"])
            if how is None:
                continue
            subjects = sorted(newer["items"], key=lambda i: (i["date"], i["id"]))
            compared = sorted(est["items"], key=lambda i: (i["date"], i["id"]))[-3:]
            sim = similarity_percent(est["key"], newer["key"])
            reason = (f"Payments to \"{newer['name']}\" (first seen {newer['first_seen']}) go to a name that reads like the established payee "
                      f"\"{est['name']}\" (first seen {est['first_seen']}): "
                      + ("they are the same once look-alike characters such as 0/o and 1/l are folded." if how == "characters" else f"{sim}% similar.")
                      + " It may be an impostor, or a real, separate business.")
            cur = subjects[0]["currency"]
            amount = sum(i["amount_minor"] for i in subjects if i["currency"] == cur)
            out.append(_candidate("lookalike_payee", f"lookalike_payee:{est['key']}|{newer['key']}", reason, cur, amount, subjects,
                                  compared or [{"type": "party", "id": est["party_id"], "payee": est["name"], "date": est["first_seen"]}],
                                  {"established_name": est["name"], "lookalike_name": newer["name"], "match": how, "similarity_percent": sim},
                                  severity="high" if how == "characters" else "medium", party_id=newer["party_id"]))
            break
    return out


def destination_changes(destinations: list[dict], open_payments: list[dict]) -> list[dict]:
    """destinations: [{id, party_id, party, kind, institution, last4, number_hmac, change_source, recorded_by, date}] oldest first;
    open_payments: open supplier-payment items with party_id."""
    out = []
    by_party = defaultdict(list)
    for d in destinations:
        by_party[d["party_id"]].append(d)
    for party_id, ds in by_party.items():
        for before, after in zip(ds, ds[1:]):
            if before["number_hmac"] == after["number_hmac"] and before["kind"] == after["kind"]:
                continue
            held = sorted((p for p in open_payments if p.get("party_id") == party_id), key=lambda p: (p["date"], p["id"]))
            snap = lambda d: {"type": "payee_destination", "id": d["id"], "date": d["date"], "payee": d["party"], "kind": d["kind"],
                              "institution": d["institution"], "masked": f"••••{d['last4']}", "change_source": d["change_source"]}
            src = after["change_source"].replace("_", " ")
            reason = (f"The destination for {after['party']} changed on {after['date']} from {before['institution']} ••••{before['last4']} to "
                      f"{after['institution']} ••••{after['last4']} (received by {src}). Payments to {after['party']} are held until someone other "
                      f"than the person who recorded it confirms the change on a contact already on file.")
            cur = held[0]["currency"] if held else "BDT"
            out.append(_candidate("destination_change", f"destination_change:{after['id']}", reason, cur,
                                  sum(p["amount_minor"] for p in held if p["currency"] == cur), [snap(after)] + held, [snap(before)],
                                  {"recorded_by": after["recorded_by"]}, party_id=party_id, destination_id=after["id"],
                                  hold=[p["id"] for p in held]))
    return out


def unsettled_payments(allocations: list[dict], movements: list[dict], as_of: date) -> list[dict]:
    """allocations: unlinked, unreversed payments [{id, obligation_id, direction, document_number, party, amount_minor, currency, date}];
    movements: ledger rows not linked to any allocation and not own-account transfers [{id, amount_minor (signed), currency, date}]."""
    out = []
    for a in allocations:
        ad = date.fromisoformat(a["date"])
        if (as_of - ad).days < SETTLEMENT_LAG_DAYS:
            continue
        sign = 1 if a["direction"] == "receivable" else -1
        if any(m["currency"] == a["currency"] and m["amount_minor"] == sign * a["amount_minor"]
               and abs((date.fromisoformat(m["date"]) - ad).days) <= SETTLEMENT_MATCH_DAYS for m in movements):
            continue
        what = "invoice" if a["direction"] == "receivable" else "bill"
        reason = (f"A payment of {fmt(a['amount_minor'], a['currency'])} was recorded on {what} {a['document_number']} ({a['party']}) on {a['date']}, "
                  f"but no ledger movement is linked to it and none of that amount appears within {SETTLEMENT_MATCH_DAYS} days.")
        subject = {"type": "allocation", "id": a["id"], "date": a["date"], "payee": a["party"], "amount_minor": a["amount_minor"],
                   "currency": a["currency"], "reference": a["document_number"], "direction": a["direction"]}
        out.append(_candidate("paid_without_settlement", f"paid_without_settlement:{a['id']}", reason, a["currency"], a["amount_minor"],
                              [subject], [{"type": "obligation", "id": a["obligation_id"], "reference": a["document_number"], "payee": a["party"]}],
                              {"direction": a["direction"], "lag_days": SETTLEMENT_LAG_DAYS, "match_days": SETTLEMENT_MATCH_DAYS}))
    return out


def evaluate(*, as_of: date, items: list[dict], parties: list[dict], destinations: list[dict], allocations: list[dict], movements: list[dict]) -> list[dict]:
    """All candidates, in a stable order. Items: [{type, id, date, payee, payee_key, amount_minor (positive), currency, reference,
    account_id, account, status, party_id}]."""
    window = (as_of - timedelta(days=HISTORY_DAYS)).isoformat()
    items = [i for i in items if i["date"] >= window]
    open_payments = [i for i in items if i["type"] == "action_proposal"]
    found = (duplicate_payments(items) + unusual_amounts(items) + split_payments(items) + lookalike_payees(items, parties)
             + destination_changes(destinations, open_payments) + unsettled_payments(allocations, movements, as_of))
    return sorted(found, key=lambda c: (list(RULES).index(c["rule"]), c["dedup_key"]))

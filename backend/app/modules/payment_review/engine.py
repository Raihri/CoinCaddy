"""Payment Review engine, calculation version payment-review-v1. Pure: frozen Cash Outlook inputs + a request in, four paths out.

One proposed payment from one named account is run through four paths on the SAME frozen inputs:

  on_time          the Cash Outlook expected path (receipts on their due dates, overdue receipts assumed in
                   7 days and marked uncertain, disputed receipts left out, payables on their due dates)
  receipts_delayed the Cash Outlook cautious path (receipts 14 days late, overdue receipts left out, stale
                   balances not relied on)
  largest_missed   the expected path with the single largest eligible receipt (the biggest receipt event
                   the expected path counts, in the payment currency) never arriving
  adverse          the expected path under the OWNER-DEFINED case: every receipt delayed N days, receipts
                   cut by a percentage (rounded down), and/or one extra outflow on a date

Only accounts in the payment's currency are used; currencies are never combined. Every path starts from the
same verified balances: a stale balance is not guessed away but reported as a blocker (below), so a stale
funding account yields "more information needed" rather than an invented shortfall. The reserve is the
floor (zero if the business has no reserve in that currency, which is then reported as a blocker).
Timing convention: on each day outflows are applied BEFORE inflows, so a day's lowest point is the
opening balance minus that day's outflows; a daily closing chart cannot show that. A linked payable's
own event is REMOVED and replaced by this payment (any unpaid remainder stays at its original date),
so it is never counted twice.

Three separate states are returned: arithmetic (within reserve / shortfall), evidence (complete /
incomplete), and exactly one decision. A known breach is never hidden: any breach on any path makes the
decision "reserve_shortfall" even if evidence is also incomplete. With no breach, a material unknown
(a funding-currency account balance missing (funding account) or stale, an unmatched own-account transfer, a disputed linked payable,
no reserve defined) makes it "more_information_needed". Only with neither is it "within_reserve", and
that is always "under the stated assumptions". The result never authorizes or initiates a payment.
"""

from collections import defaultdict
from datetime import date, timedelta

from app.modules.outlook.engine import CAUTIOUS_DELAY_DAYS, HORIZON_DAYS, OVERDUE_ASSUMED_DAYS, build_events, opening

CALC_VERSION = "payment-review-v1"
SCENARIOS = (("on_time", "All on time"), ("receipts_delayed", "Receipts delayed"),
             ("largest_missed", "Largest receipt missed"), ("adverse", "Owner-defined adverse"))


class ReviewError(ValueError):
    """The request cannot be modelled on these inputs (shown to the owner as a 422)."""


def _d(s: str) -> date:
    return date.fromisoformat(s)


def validate(inputs: dict, req: dict) -> tuple[dict, dict | None]:
    """Return (funding account, linked payable or None) or raise ReviewError."""
    as_of = _d(inputs["as_of"])
    acc = next((a for a in inputs["accounts"] if a["id"] == req["account_id"]), None)
    if acc is None:
        raise ReviewError("The funding account is not an account of this business.")
    if not as_of < _d(req["payment_date"]) <= as_of + timedelta(days=HORIZON_DAYS):
        raise ReviewError(f"The payment date must be after {inputs['as_of']} and within the {HORIZON_DAYS}-day horizon.")
    adv = req["adverse"]
    if not (adv["receipt_delay_days"] or adv["receipts_haircut_percent"] or adv["extra_outflow_minor"]):
        raise ReviewError("Define the adverse case: a receipt delay, a receipt reduction, or an extra outflow.")
    if adv["extra_outflow_minor"] and adv["extra_outflow_date"] is None:
        raise ReviewError("An extra outflow needs a date.")
    if adv["extra_outflow_minor"] and not as_of < _d(adv["extra_outflow_date"]) <= as_of + timedelta(days=HORIZON_DAYS):
        raise ReviewError("The extra outflow date must be inside the forecast horizon.")
    linked = None
    if req.get("linked_payable_id"):
        linked = next((o for o in inputs["obligations"] if o["id"] == req["linked_payable_id"]), None)
        if linked is None or linked["direction"] != "payable":
            raise ReviewError("The linked payable is not an outstanding bill of this business.")
        if linked["currency"] != acc["currency"]:
            raise ReviewError("The linked payable and the funding account use different currencies.")
        if req["amount_minor"] > linked["outstanding_minor"]:
            raise ReviewError("The payment is larger than the amount still outstanding on the linked payable.")
    return acc, linked


def _paths_events(inputs: dict, req: dict, acc: dict, linked: dict | None) -> dict[str, list[dict]]:
    cur = acc["currency"]
    base_events, _ = build_events(inputs)
    base_events = [e for e in base_events if e["currency"] == cur]
    adv = req["adverse"]
    pay_date = req["payment_date"]
    payee = req.get("payee") or (linked["party"] if linked else "the payee")
    paths: dict[str, list[dict]] = {}

    def own(label, amount, when, source_id, source):
        return {"source": source, "source_id": source_id, "label": label, "amount_minor": amount, "date": when, "note": None, "uncertain": False,
                "direction": "payment_review", "account_id": None}

    for key, _label in SCENARIOS:
        kind = "cautious_date" if key == "receipts_delayed" else "expected_date"
        events = []
        removed_id, linked_account = None, acc["id"]
        if key == "largest_missed":
            eligible = [e for e in base_events if e["direction"] == "receivable" and e["expected_date"] and e["amount_minor"] > 0]
            if eligible:
                removed_id = min(eligible, key=lambda e: (-e["amount_minor"], e["expected_date"], e["source_id"]))["source_id"]
        for e in base_events:
            if linked and e["source_id"] == linked["id"]:
                linked_account = e["account_id"]
                continue                                   # replaced by this payment, never counted twice
            if e["source_id"] == removed_id or not e[kind]:
                continue
            when, amount = e[kind], e["amount_minor"]
            if key == "adverse" and e["direction"] == "receivable":
                when = (_d(when) + timedelta(days=adv["receipt_delay_days"])).isoformat()
                amount = amount * (100 - adv["receipts_haircut_percent"]) // 100
            events.append({"source": e["source"], "source_id": e["source_id"], "label": e["label"], "amount_minor": amount, "date": when,
                           "note": e["note"], "uncertain": e["uncertain"], "direction": e["direction"], "account_id": e["account_id"]})
        if linked and req["amount_minor"] < linked["outstanding_minor"]:
            when = max(_d(linked["due_date"]), _d(inputs["as_of"]) + timedelta(days=1)).isoformat()
            events.append({"source": "obligation", "source_id": linked["id"], "label": f"{linked['party']} {linked['document_number']} (unpaid remainder)",
                           "amount_minor": -(linked["outstanding_minor"] - req["amount_minor"]), "date": when, "note": "Remainder not covered by this payment",
                           "uncertain": False, "direction": "payable", "account_id": linked_account})
        pay_label = f"Proposed payment to {payee}" + (f" ({linked['document_number']})" if linked else "")
        events.append({**own(pay_label, -req["amount_minor"], pay_date, linked["id"] if linked else "proposed-payment", "proposed_payment"), "account_id": acc["id"]})
        if key == "adverse" and adv["extra_outflow_minor"]:
            events.append(own("Owner-defined extra outflow", -adv["extra_outflow_minor"], adv["extra_outflow_date"], "adverse-extra-outflow", "owner_assumption"))
        paths[key] = events
    return paths


def _series(opening_value: int, events: list[dict], as_of: date) -> list[tuple[int, int]]:
    """Per day: (lowest point with outflows first, closing balance)."""
    outs, ins = defaultdict(int), defaultdict(int)
    for e in events:
        (outs if e["amount_minor"] < 0 else ins)[e["date"]] += e["amount_minor"]
    bal, series = opening_value, []
    for i in range(1, HORIZON_DAYS + 1):
        day = (as_of + timedelta(days=i)).isoformat()
        low = bal + outs[day]
        bal = low + ins[day]
        series.append((low, bal))
    return series


def _assumptions(key: str, req: dict, rules_delay: int, overdue_days: int) -> list[str]:
    adv = req["adverse"]
    common = ["Payables are paid on their due dates (overdue ones tomorrow); disputed receivables are not counted.",
              "Within a day, outflows are applied before inflows."]
    return {
        "on_time": ["Receipts arrive on their due dates; overdue receipts are assumed collected in " + f"{overdue_days} days (uncertain)."] + common,
        "receipts_delayed": [f"Receipts arrive {rules_delay} days after their due dates; overdue receipts are not counted; stale balances are not relied on."] + common,
        "largest_missed": ["As on time, but the single largest eligible receipt never arrives."] + common,
        "adverse": [f"As on time, with every receipt delayed {adv['receipt_delay_days']} day(s), receipts reduced by {adv['receipts_haircut_percent']}%"
                    + (f", and an extra outflow of {adv['extra_outflow_minor']} minor units on {adv['extra_outflow_date']}." if adv["extra_outflow_minor"] else ".")] + common,
    }[key]


def review(inputs: dict, req: dict) -> dict:
    acc, linked = validate(inputs, req)
    cur = acc["currency"]
    accounts = [a for a in inputs["accounts"] if a["currency"] == cur and a["usable_minor"] is not None]
    reserve_defined = inputs["reserve"]["currency"] == cur
    floor = inputs["reserve"]["amount_minor"] if reserve_defined else 0
    events_by_path = _paths_events(inputs, req, acc, linked)
    if acc["usable_minor"] is None:
        # Total cash is unknown, so no minimum or breach can honestly be stated: nothing is assessed.
        results = [{"key": key, "label": label, "minimum": None, "first_breach": None, "shortfall_minor": None, "funding_account_minimum": None,
                    "funding_account_overdrawn": False, "status": "not_assessed", "assumptions": _assumptions(key, req, CAUTIOUS_DELAY_DAYS, OVERDUE_ASSUMED_DAYS),
                    "events": sorted(events_by_path[key], key=lambda e: (e["date"], e["source_id"])),
                    "links": [{"kind": "account", "id": acc["id"], "label": acc["label"]}]} for key, label in SCENARIOS]
        return _finish(inputs, req, acc, linked, cur, floor, reserve_defined, results, events_by_path)
    return _finish(inputs, req, acc, linked, cur, floor, reserve_defined, _assess(inputs, req, acc, linked, accounts, floor, events_by_path), events_by_path)


def _assess(inputs, req, acc, linked, accounts, floor, events_by_path) -> list[dict]:
    as_of = _d(inputs["as_of"])
    with_balance = {a["id"] for a in accounts}
    results = []
    for key, label in SCENARIOS:
        opening_total = sum(opening(a)[0] for a in accounts)
        events = [e for e in events_by_path[key] if e["account_id"] in with_balance or e["account_id"] is None or e["source"] == "proposed_payment"]
        total = _series(opening_total, events, as_of)
        lows = [min(low, close) for low, close in total]
        i_min = min(range(HORIZON_DAYS), key=lambda i: (lows[i], i))
        i_breach = next((i for i in range(HORIZON_DAYS) if lows[i] < floor), None)
        minimum_day = (as_of + timedelta(days=i_min + 1)).isoformat()
        funding = None
        if acc["id"] in with_balance:
            fs = _series(opening(acc)[0], [e for e in events if e["account_id"] == acc["id"]], as_of)
            fl = [min(a, b) for a, b in fs]
            j = min(range(HORIZON_DAYS), key=lambda i: (fl[i], i))
            funding = {"date": (as_of + timedelta(days=j + 1)).isoformat(), "amount_minor": fl[j]}
        overdrawn = funding is not None and funding["amount_minor"] < 0
        breach = None if i_breach is None else {"date": (as_of + timedelta(days=i_breach + 1)).isoformat(), "balance_minor": lows[i_breach],
                                               "gap_minor": floor - lows[i_breach]}
        cutoff = (breach or {"date": minimum_day})["date"]
        links, seen = [{"kind": "account", "id": acc["id"], "label": acc["label"]}], set()
        if linked:
            seen.add(("obligation", linked["id"]))
            links.append({"kind": "obligation", "id": linked["id"], "label": f"{linked['party']} {linked['document_number']}"})
        for e in sorted(events, key=lambda e: (e["date"], e["source_id"])):
            kind = {"obligation": "obligation", "restricted_fund": "restricted_fund"}.get(e["source"])
            if kind and e["date"] <= cutoff and (kind, e["source_id"]) not in seen:
                seen.add((kind, e["source_id"]))
                links.append({"kind": kind, "id": e["source_id"], "label": e["label"]})
        results.append({
            "key": key, "label": label,
            "minimum": {"date": minimum_day, "amount_minor": lows[i_min]},
            "first_breach": breach, "shortfall_minor": max(0, floor - lows[i_min]),
            "funding_account_minimum": funding, "funding_account_overdrawn": overdrawn,
            "status": "shortfall" if (breach or overdrawn) else "within_reserve",
            "assumptions": _assumptions(key, req, CAUTIOUS_DELAY_DAYS, OVERDUE_ASSUMED_DAYS),
            "events": sorted(events, key=lambda e: (e["date"], e["source_id"])), "links": links,
        })

    return results


def _finish(inputs, req, acc, linked, cur, floor, reserve_defined, results, events_by_path) -> dict:
    accounts = [a for a in inputs["accounts"] if a["currency"] == cur and a["usable_minor"] is not None]
    blockers = []
    if not reserve_defined:
        blockers.append({"code": "no_reserve_in_currency", "message": f"No reserve is defined in {cur}, so \"within reserve\" cannot be judged; zero was used as the floor.", "source_id": None})
    if acc["usable_minor"] is None:
        blockers.append({"code": "funding_balance_missing", "message": f"{acc['label']} has no verified balance. Enter a balance anchor before relying on this review.", "source_id": acc["id"]})
    for a in accounts:  # every balance in this currency feeds the totals, so a stale one is a material unknown
        if a["freshness"] != "fresh":
            who = "The funding account" if a["id"] == acc["id"] else "An account in this currency"
            blockers.append({"code": "funding_balance_stale" if a["id"] == acc["id"] else "account_balance_stale",
                             "message": f"{who} {a['label']} is verified only through {a['evidence_through']}. Verify its current balance before relying on this review.",
                             "source_id": a["id"]})
    if linked and linked["disputed"]:
        blockers.append({"code": "linked_payable_disputed", "message": f"{linked['party']} {linked['document_number']} is disputed. Resolve the dispute before paying it.", "source_id": linked["id"]})
    for t in inputs.get("unmatched_transfers", []):
        blockers.append({"code": "unmatched_transfer", "message": "An own-account transfer has a missing side, so an account balance may be incomplete. Link or void it.", "source_id": t["id"]})

    known_breach = any(r["status"] == "shortfall" for r in results)
    decision = "reserve_shortfall" if known_breach else "more_information_needed" if blockers else "within_reserve"
    arithmetic = "not_assessed" if all(r["status"] == "not_assessed" for r in results) else "reserve_shortfall" if known_breach else "within_reserve"
    notes = [{"code": "uncertain_receipt", "message": f"{e['label']} is overdue; the on-time path assumes it is collected.", "source_id": e["source_id"]}
             for e in events_by_path["on_time"] if e["uncertain"] and e["amount_minor"] > 0]
    notes += [{"code": "other_account", "message": f"{a['label']} has no usable balance and is left out.", "source_id": a["id"]}
              for a in inputs["accounts"] if a["currency"] == cur and a["usable_minor"] is None and a["id"] != acc["id"]]
    return {
        "calc_version": CALC_VERSION, "as_of": inputs["as_of"], "currency": cur, "floor_minor": floor, "reserve_defined": reserve_defined,
        "payment": {"account_id": acc["id"], "account_label": acc["label"], "amount_minor": req["amount_minor"], "date": req["payment_date"],
                    "payee": req.get("payee") or (linked["party"] if linked else None),
                    "linked_payable": None if linked is None else {"id": linked["id"], "party": linked["party"], "document_number": linked["document_number"],
                                                                   "outstanding_minor": linked["outstanding_minor"]}},
        "paths": results,
        "arithmetic_state": arithmetic,
        "evidence_state": "incomplete" if blockers else "complete",
        "decision": decision, "blockers": blockers, "notes": notes,
        "authorization": "This review is information only. It does not approve, authorize, or initiate any payment.",
    }

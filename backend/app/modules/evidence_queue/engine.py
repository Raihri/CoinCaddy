"""Decision-Impact Evidence Queue, version evidence-queue-v1. Pure: a saved Payment Review in, ranked verification tasks out.

The queue explains which missing, stale, or disputed records could change THIS review, and in what order
to verify them. It never changes the review, never resolves evidence, and never produces a payment result:

* The review's decision stays exactly as saved. A hypothetical figure is only ever "what the review
  would show if this record were verified in the best (or worst) way"; it is not a result.
* Effects are measured by re-running the unmodified Payment Review calculator on a copy of the review's
  own frozen inputs with ONE record changed. Nothing is estimated outside that calculator.
* An effect is bounded only where the record carries a defensible bound (an invoice or bill amount). A
  missing or stale balance, or an unmatched transfer, has no defensible amount, so it is reported as
  unbounded rather than guessed. Those are also exactly what blocks a positive result, so they rank first.

Ranking (first = verify first):
  1 blocking            a material unknown that blocks a positive result (funding balance first, then a
                        disputed linked bill, other stale balances, unmatched transfers, no reserve)
  2 decision_changing   a bounded record whose best or worst outcome would change the displayed decision
  3 material            a bounded record that moves the lowest cash on some path by at least the
                        materiality threshold
  4 unbounded_upside    a missing balance that is left out (so the review is conservative) and could only add cash
  5 immaterial          moves the lowest cash by less than the threshold on every path
Inside a tier: larger swing first, then larger amount, then record id (so the order is deterministic).
Materiality threshold = 1% of the reserve floor, or 1% of the payment if there is no reserve (at least 1 minor unit).
"""

import copy
from datetime import date

from app.modules.payment_review.engine import review

VERSION = "evidence-queue-v1"
TIERS = {"blocking": 1, "decision_changing": 2, "material": 3, "unbounded_upside": 4, "immaterial": 5}
DECISION_WORDS = {"within_reserve": "within reserve", "reserve_shortfall": "a reserve shortfall", "more_information_needed": "more information needed"}


def _mins(res: dict) -> dict[str, int | None]:
    return {p["key"]: (p["minimum"]["amount_minor"] if p["minimum"] else None) for p in res["paths"]}


def _variant(inputs: dict, request: dict, edit) -> dict | None:
    changed = copy.deepcopy(inputs)
    edit(changed)
    try:
        return review(changed, request)
    except ValueError:  # the changed copy cannot be modelled; report no bound rather than a guess
        return None


def _snapshot(res: dict) -> dict:
    return {"decision": res["decision"], "minimum_by_path": _mins(res)}


def _effect(base: dict, best: dict | None, worst: dict | None) -> dict:
    cur = _mins(base)
    out = {"bounded": best is not None and worst is not None, "current": {"decision": base["decision"], "minimum_by_path": cur},
           "best": _snapshot(best) if best else None, "worst": _snapshot(worst) if worst else None, "swing_minor": None,
           "flips_decision": False, "best_decision": best["decision"] if best else None, "worst_decision": worst["decision"] if worst else None}
    if best is None or worst is None:
        return out
    swing = 0
    for key, c in cur.items():
        vals = [v for v in (_mins(best)[key], _mins(worst)[key], c) if v is not None]
        if vals and c is not None:
            swing = max(swing, max(vals) - min(vals))
    out["swing_minor"] = swing
    out["flips_decision"] = best["decision"] != base["decision"] or worst["decision"] != base["decision"]
    return out


def _task(key, kind, tier, priority, title, why, verify, source, amount, effect, blocks, **extra) -> dict:
    return {"id": key, "kind": kind, "tier": tier, "priority": priority, "title": title, "why": why, "verify": verify, "source": source,
            "amount_minor": amount, "effect": effect, "blocks_positive_result": blocks, "status": "open", "status_note": None, **extra}


def build_queue(inputs: dict, request: dict, saved: dict, current_inputs: dict | None = None) -> dict:
    """inputs/request/saved are the stored frozen inputs, request, and results of one Payment Review."""
    base = review(inputs, request)          # re-derived from the frozen inputs; must equal what was saved
    cur = saved["currency"]
    as_of = date.fromisoformat(inputs["as_of"])
    floor = saved["floor_minor"]
    threshold = max(1, (floor if floor else saved["payment"]["amount_minor"]) // 100)
    funding_id = saved["payment"]["account_id"]
    accounts = {a["id"]: a for a in inputs["accounts"]}
    tasks: list[dict] = []
    no_effect = {"bounded": False, "current": {"decision": base["decision"], "minimum_by_path": _mins(base)}, "best": None, "worst": None,
                 "swing_minor": None, "flips_decision": False, "best_decision": None, "worst_decision": None}

    for b in saved["blockers"]:
        sid = b["source_id"]
        if b["code"] in ("funding_balance_missing", "funding_balance_stale", "account_balance_stale"):
            a = accounts[sid]
            missing = b["code"] == "funding_balance_missing"
            tasks.append(_task(
                f"balance:{sid}", "balance", "blocking", 1 if sid == funding_id else 3,
                f"{'Enter' if missing else 'Verify'} the balance of {a['label']}",
                (f"{a['label']} funds this payment and has no verified balance, so no path can be judged." if missing else
                 f"{a['label']} was last verified {a['evidence_through']}; every path starts from that balance, so none can be trusted for a positive result.")
                if sid == funding_id else
                f"{a['label']} is in the same currency and was last verified {a['evidence_through']}; its cash feeds every path.",
                f"Check {a['label']} against a statement and enter a balance anchor.",
                {"kind": "account", "id": sid, "label": a["label"]}, a["usable_minor"], {**no_effect, "note": "The size of the change cannot be bounded."}, True))
        elif b["code"] == "linked_payable_disputed":
            lp = next(o for o in inputs["obligations"] if o["id"] == sid)
            best = _variant(inputs, request, lambda i: next(o for o in i["obligations"] if o["id"] == sid).update(disputed=False))
            tasks.append(_task(
                f"dispute:{sid}", "disputed_linked_payable", "blocking", 2, f"Resolve the dispute on {lp['party']} {lp['document_number']}",
                "The bill this payment settles is disputed. Paying a disputed bill is not something this review can approve.",
                "Resolve the dispute with the supplier, then clear it on the bill.", {"kind": "obligation", "id": sid, "label": f"{lp['party']} {lp['document_number']}"},
                lp["outstanding_minor"], _effect(base, best, base) if best else no_effect, True))
        elif b["code"] == "unmatched_transfer":
            tasks.append(_task(
                f"transfer:{sid}", "unmatched_transfer", "blocking", 4, f"Link or void own-account transfer {str(sid)[:8]} (one side is missing)",
                "One side of an own-account transfer is missing, so an account balance may be incomplete. It cannot be tied to one account, so it blocks a positive result.",
                "Find the other side of the transfer in the statements and link it, or void the declaration.",
                {"kind": "transfer", "id": sid, "label": "Own-account transfer"}, None, {**no_effect, "note": "The amount is not part of the frozen inputs, so no bound is given."}, True))
        elif b["code"] == "no_reserve_in_currency":
            tasks.append(_task(
                "reserve", "no_reserve", "blocking", 5, f"Set a reserve in {cur}", f"No reserve is defined in {cur}, so \"within reserve\" cannot be judged.",
                "An owner sets the reserve in business settings.", {"kind": "business_settings", "id": "reserve", "label": "Business settings"}, None,
                {**no_effect, "note": "No bound: zero was used as the floor."}, True))

    for o in inputs["obligations"]:
        if o["currency"] != cur or o["id"] == (request.get("linked_payable_id") or ""):
            continue
        src = {"kind": "obligation", "id": o["id"], "label": f"{o['party']} {o['document_number']}"}
        if o["direction"] == "receivable" and o["disputed"]:
            best = _variant(inputs, request, lambda i, oid=o["id"]: next(x for x in i["obligations"] if x["id"] == oid).update(disputed=False))
            tasks.append(_task(
                f"dispute:{o['id']}", "disputed_receivable", "material", 6, f"Resolve the dispute on invoice {o['document_number']} ({o['party']})",
                "A disputed receivable is not counted on any path. If it is upheld and paid, cash would arrive; if not, nothing changes.",
                "Resolve the dispute with the customer, then clear it on the invoice.", src, o["outstanding_minor"], _effect(base, best, base) if best else no_effect, False))
        elif o["direction"] == "receivable" and date.fromisoformat(o["due_date"]) < as_of:
            worst = _variant(inputs, request, lambda i, oid=o["id"]: i["obligations"].remove(next(x for x in i["obligations"] if x["id"] == oid)))
            tasks.append(_task(
                f"overdue:{o['id']}", "uncertain_receipt", "material", 7, f"Confirm when overdue invoice {o['document_number']} ({o['party']}) will be paid",
                "The on-time, largest-missed, and adverse paths assume this overdue invoice is collected 7 days after the review date; the delayed path leaves it out.",
                "Ask the customer for a payment date and record it, or mark the invoice disputed if it will not be paid.", src, o["outstanding_minor"],
                _effect(base, base, worst) if worst else no_effect, False))
        elif o["direction"] == "payable" and o["disputed"]:
            best = _variant(inputs, request, lambda i, oid=o["id"]: i["obligations"].remove(next(x for x in i["obligations"] if x["id"] == oid)))
            tasks.append(_task(
                f"dispute:{o['id']}", "disputed_payable", "material", 8, f"Resolve the dispute on bill {o['document_number']} ({o['party']})",
                "A disputed bill is still counted as an outflow on every path. If the dispute removes it, the lowest cash would be higher.",
                "Resolve the dispute with the supplier and record the outcome.", src, o["outstanding_minor"], _effect(base, best, base) if best else no_effect, False))

    for a in inputs["accounts"]:
        if a["currency"] == cur and a["usable_minor"] is None and a["id"] != funding_id:
            tasks.append(_task(
                f"balance:{a['id']}", "balance", "unbounded_upside", 9, f"Enter a balance for {a['label']}",
                f"{a['label']} has no verified balance and is left out of every path, so the review is conservative here. Its cash could only help.",
                f"Check {a['label']} against a statement and enter a balance anchor.", {"kind": "account", "id": a["id"], "label": a["label"]}, None,
                {**no_effect, "note": "Left out, so verifying it could only add cash; the amount cannot be bounded."}, False))

    for t in tasks:
        e = t["effect"]
        if t["tier"] == "material":
            t["tier"] = "decision_changing" if e["flips_decision"] else "material" if (e["swing_minor"] or 0) >= threshold else "immaterial"
        t["tier_rank"] = TIERS[t["tier"]]
    tasks.sort(key=lambda t: (t["tier_rank"], t["priority"] if t["tier"] in ("blocking", "unbounded_upside") else 0,
                              -(t["effect"]["swing_minor"] or 0), -(t["amount_minor"] or 0), t["id"]))
    for i, t in enumerate(tasks, 1):
        t["rank"] = i

    changed_since = None
    if current_inputs is not None:
        changed_since = _mark_changes(tasks, inputs, current_inputs, saved)
    return {"version": VERSION, "decision_on_record": saved["decision"], "currency": cur, "materiality_threshold_minor": threshold,
            "reproduces_saved_review": base["decision"] == saved["decision"] and _mins(base) == _mins(saved),
            "records_changed_since_review": changed_since, "tasks": tasks,
            "note": "This queue never changes the review or resolves any record. The decision above changes only when records are reviewed and corrected by someone with permission and a new Payment Review is run."}


def _mark_changes(tasks: list[dict], frozen: dict, now: dict, saved: dict) -> bool:
    accts = {a["id"]: a for a in now["accounts"]}
    obs = {o["id"]: o for o in now["obligations"]}
    unmatched = {t["id"] for t in now.get("unmatched_transfers", [])}
    fa, fo = {a["id"]: a for a in frozen["accounts"]}, {o["id"]: o for o in frozen["obligations"]}
    any_changed = False
    for t in tasks:
        sid, kind, changed = t["source"]["id"], t["kind"], False
        if kind == "balance":
            a, b = fa[sid], accts.get(sid)
            changed = b is None or (a["usable_minor"], a["freshness"], a["evidence_through"]) != (b["usable_minor"], b["freshness"], b["evidence_through"])
        elif kind == "unmatched_transfer":
            changed = sid not in unmatched
        elif kind in ("disputed_linked_payable", "disputed_receivable", "disputed_payable", "uncertain_receipt"):
            o = obs.get(sid)
            changed = o is None or (fo[sid]["disputed"], fo[sid]["outstanding_minor"], fo[sid]["due_date"]) != (o["disputed"], o["outstanding_minor"], o["due_date"])
        elif kind == "no_reserve":
            changed = now["reserve"]["currency"] == saved["currency"]
        if changed:
            t["status"] = "record_changed"
            t["status_note"] = "This record has changed since the review. Run a new Payment Review to see the effect; this review and its decision are unchanged."
            any_changed = True
    return any_changed

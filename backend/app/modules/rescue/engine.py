"""Cash Gap Rescue Plan engine, calculation version rescue-v1. Pure: frozen inputs in, compared proposals out.

A rescue plan starts from one saved Payment Review that shows a reserve shortfall on some path. It takes
the review's OWN frozen forecast inputs and request, changes one assumption per option on a copy, and
re-runs the unmodified Payment Review calculator, so every figure is on the same frozen forecast:

  collect      an overdue, undisputed customer invoice is paid (all or a percentage) on a date. The collected
               amount arrives on that date on every path: it is the owner's assumption, listed as a dependency.
  move         a DISCRETIONARY bill (marketing or other expense, never rent, salaries, tax, loan repayment or
               inventory) is paid later, only when a permission from the supplier is documented, and no later
               than the documented date. The bill is still owed: it simply falls due on the new date.
  financing    one eligible product from the latest saved Funding Match (illustrative terms): net proceeds on
               the funding date, every repayment on its date.
  combination  every available option above together (only when at least two are available).

For each option: lowest cash and date, first reserve breach, remaining shortfall, when it takes effect, total
financing cost, and unresolved dependencies, on the chosen path (default: the review's worst shortfall path).
An option that is not available is listed with the reason and never simulated.

Recommendation: a proposal only. An option qualifies if it leaves NO shortfall on the chosen path. Qualifying
options are preferred by (no borrowing, fewer unresolved dependencies, lower financing cost, earlier effect,
fewer actions). If the review still has unverified material evidence, or no option closes the gap, NO option is
recommended and the plan says so; the closest option is shown as information, never as a safe answer.
Nothing here changes the ledger, a bill, an invoice, or any external system.
"""

import copy
from datetime import date, timedelta

from app.modules.funding.engine import _schedule
from app.modules.payment_review.engine import HORIZON_DAYS, SCENARIOS, ReviewError, review

CALC_VERSION = "rescue-v1"
DISCRETIONARY = ("marketing", "other_expense")
DEFAULT_COLLECT_DAYS = 2
PATH_KEYS = tuple(k for k, _ in SCENARIOS)


class PlanError(ValueError):
    """The plan cannot be built on these inputs (shown to the owner as a 422)."""


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _path(res: dict, key: str) -> dict:
    return next(p for p in res["paths"] if p["key"] == key)


def _find(inputs: dict, oid: str) -> dict:
    return next(o for o in inputs["outlook"]["obligations"] if o["id"] == oid)


def _release(ins: dict, account_id: str, rid: str, amount: int, on: date, reason: str) -> None:
    next(a for a in ins["accounts"] if a["id"] == account_id)["restriction_releases"].append(
        {"id": rid, "amount_minor": amount, "reason": reason, "effective_to": (on - timedelta(days=1)).isoformat()})


def _with_balance(ins: dict, preferred: str | None, fallback: str) -> str:
    acc = next((a for a in ins["accounts"] if a["id"] == preferred), None)
    return preferred if acc and acc["usable_minor"] is not None else fallback


def resolve_assumptions(inputs: dict) -> tuple[dict, dict, dict]:
    """Return (effective assumptions, candidates, unavailable options). Raises PlanError on a bad adjustment."""
    as_of = _d(inputs["outlook"]["as_of"])
    req, adj = inputs["request"], inputs["adjustments"]
    cur = inputs["currency"]
    linked = req.get("linked_payable_id")
    meta = {o["id"]: o for o in inputs["obligations_meta"] if o["currency"] == cur}
    permits = {p["obligation_id"]: p for p in inputs["permissions"]}
    unavailable: dict[str, str] = {}

    collectable = sorted((o for o in meta.values() if o["direction"] == "receivable" and not o["disputed"] and o["days_overdue"] > 0 and o["outstanding_minor"] > 0),
                         key=lambda o: (-o["outstanding_minor"], o["due_date"], o["id"]))
    movable, no_permission = [], []
    for o in sorted(meta.values(), key=lambda o: (-o["outstanding_minor"], o["id"])):
        if o["direction"] != "payable" or o["id"] == linked or o["category"] not in DISCRETIONARY or o["outstanding_minor"] <= 0:
            continue
        p = permits.get(o["id"])
        (movable if p and _d(p["agreed_due_date"]) > _d(o["due_date"]) else no_permission).append(o)
    chosen = {}

    if adj.get("collect_obligation_id"):
        chosen["collect"] = next((o for o in collectable if o["id"] == adj["collect_obligation_id"]), None)
        if chosen["collect"] is None:
            raise PlanError("That invoice is not an overdue, undisputed invoice that can be collected.")
    elif collectable:
        chosen["collect"] = collectable[0]
    else:
        unavailable["collect"] = "There is no overdue, undisputed customer invoice to collect."
    if adj.get("defer_obligation_id"):
        chosen["defer"] = next((o for o in movable if o["id"] == adj["defer_obligation_id"]), None)
        if chosen["defer"] is None:
            raise PlanError("That bill is not a discretionary bill with a documented permission to pay later.")
    elif movable:
        chosen["defer"] = movable[0]
    else:
        unavailable["defer"] = ("No discretionary bill has a documented permission to be paid later"
                                + (f" ({', '.join(o['party'] + ' ' + o['document_number'] for o in no_permission)} would need one)." if no_permission else "."))

    eff = {"path": adj.get("path"), "collect_percent": adj.get("collect_percent") or 100}
    if "collect" in chosen:
        when = _d(adj["collect_date"]) if adj.get("collect_date") else as_of + timedelta(days=DEFAULT_COLLECT_DAYS)
        if not as_of < when <= as_of + timedelta(days=HORIZON_DAYS):
            raise PlanError("The collection date must be after the review date and within the 13-week forecast.")
        eff.update(collect_obligation_id=chosen["collect"]["id"], collect_date=when.isoformat())
    if "defer" in chosen:
        permit = permits[chosen["defer"]["id"]]
        new = _d(adj["deferral_date"]) if adj.get("deferral_date") else _d(permit["agreed_due_date"])
        if not _d(chosen["defer"]["due_date"]) < new <= _d(permit["agreed_due_date"]):
            raise PlanError(f"The new payment date must be after the current due date and no later than the documented {permit['agreed_due_date']}.")
        eff.update(defer_obligation_id=chosen["defer"]["id"], deferral_date=new.isoformat())

    fund = inputs.get("funding")
    if fund is None:
        unavailable["financing"] = "No Funding Match has been saved for this currency."
    else:
        ids = fund["eligible_ids"]
        pid = adj.get("financing_product_id") or (ids[0] if ids else None)
        if not ids:
            unavailable["financing"] = "The latest Funding Match found no eligible product."
        elif pid not in ids:
            raise PlanError("That product was not eligible in the latest Funding Match.")
        else:
            eff["financing_product_id"] = pid
    return eff, chosen, unavailable


def _apply(ins: dict, kind: str, eff: dict, chosen: dict, inputs: dict) -> dict:
    """Change the copy of the outlook inputs for one option. Returns what was done (for display)."""
    as_of = _d(inputs["outlook"]["as_of"])
    funding_acc = inputs["request"]["account_id"]
    if kind == "collect":
        o = chosen["collect"]
        amount = o["outstanding_minor"] * eff["collect_percent"] // 100
        ob = next(x for x in ins["obligations"] if x["id"] == o["id"])
        ob["outstanding_minor"] -= amount
        acc = _with_balance(ins, o["settlement_account_id"], funding_acc)
        _release(ins, acc, f"rescue-collect-{o['id']}", amount, _d(eff["collect_date"]), f"Collected {o['party']} {o['document_number']}")
        return {"kind": "collect", "label": f"Collect {o['party']} invoice {o['document_number']}" + ("" if eff["collect_percent"] == 100 else f" ({eff['collect_percent']}%)"),
                "amount_minor": amount, "date": eff["collect_date"], "source": {"kind": "obligation", "id": o["id"]}}
    if kind == "defer":
        o = chosen["defer"]
        next(x for x in ins["obligations"] if x["id"] == o["id"])["due_date"] = eff["deferral_date"]
        return {"kind": "defer", "label": f"Pay {o['party']} bill {o['document_number']} on {eff['deferral_date']}", "amount_minor": o["outstanding_minor"],
                "date": eff["deferral_date"], "source": {"kind": "obligation", "id": o["id"]}}
    f = inputs["funding"]
    product = next(p for p in f["products"] if p["id"] == eff["financing_product_id"])
    disb = max(as_of + timedelta(days=product["funding_days"]), as_of + timedelta(days=1))
    terms = _schedule(product, f["amount_minor"], disb)
    acc = _with_balance(ins, f["receiving_account_id"], funding_acc)
    _release(ins, acc, "rescue-financing-proceeds", terms["net_proceeds_minor"], disb, f"{product['name']} proceeds")
    for k, r in enumerate(terms["schedule"], 1):
        ins["obligations"].append({"id": f"rescue-repayment-{k}", "direction": "payable", "party": product["name"], "document_number": f"repayment {k}", "due_date": r["date"],
                                   "outstanding_minor": r["amount_minor"], "currency": inputs["currency"], "disputed": False, "settlement_account_id": acc})
    return {"kind": "financing", "label": f"Use {product['name']} (illustrative)", "amount_minor": terms["principal_minor"], "date": terms["disbursement_date"],
            "terms": terms, "product_id": product["id"], "source": {"kind": "funding_match", "id": f["run_id"]}}


def _dependencies(kind: str, eff: dict, chosen: dict, inputs: dict, done: list[dict]) -> list[str]:
    out = []
    for d in done:
        if d["kind"] == "collect":
            o = chosen["collect"]
            out.append(f"{o['party']} must actually pay {d['amount_minor']} minor units by {d['date']} (invoice {o['document_number']} is {o['days_overdue']} day(s) overdue).")
        elif d["kind"] == "defer":
            o, p = chosen["defer"], next(p for p in inputs["permissions"] if p["obligation_id"] == chosen["defer"]["id"])
            out.append(f"{o['party']}'s agreement to be paid on {d['date']} is documented ({p['evidence_reference']}); the bill is still owed and is not cancelled.")
        else:
            out.append("Lender approval has not been requested or granted: these are illustrative sample terms.")
            out.append("Business age, documents, and security in the saved Funding Match are the owner's unverified declarations.")
    return out


def plan(inputs: dict) -> dict:
    """inputs: outlook (the review's frozen inputs), request, obligations_meta, permissions, funding|None, adjustments, currency, review (saved results)."""
    base = review(inputs["outlook"], inputs["request"])
    shortfalls = [p for p in base["paths"] if p["status"] == "shortfall"]
    if not shortfalls:
        raise PlanError("This review shows no reserve shortfall to rescue.")
    eff, chosen, unavailable = resolve_assumptions(inputs)
    target = eff["path"] or min(shortfalls, key=lambda p: (p["minimum"]["amount_minor"], PATH_KEYS.index(p["key"])))["key"]
    if target not in PATH_KEYS or _path(base, target)["status"] != "shortfall":
        raise PlanError("Choose a scenario that shows a shortfall.")
    eff["path"] = target
    bp = _path(base, target)
    floor = base["floor_minor"]
    blockers = [b["message"] for b in base["blockers"]]
    kinds = [k for k in ("collect", "defer", "financing") if k not in unavailable]

    def evaluate(oid: str, title: str, which: list[str]) -> dict | None:
        ins = copy.deepcopy(inputs["outlook"])
        done = [_apply(ins, k, eff, chosen, inputs) for k in which]
        try:
            res = review(ins, inputs["request"])
        except ReviewError as exc:
            raise PlanError(str(exc)) from exc
        p = _path(res, target)
        cost = sum(d["terms"]["total_cost_minor"] for d in done if d["kind"] == "financing")
        deps = _dependencies(oid, eff, chosen, inputs, done) + [f"Not yet verified: {b}" for b in blockers]
        return {"id": oid, "title": title, "actions": done, "borrows": any(d["kind"] == "financing" for d in done),
                "effect_date": max(d["date"] for d in done), "minimum": p["minimum"], "first_breach": p["first_breach"],
                "remaining_shortfall_minor": p["shortfall_minor"], "funding_account_overdrawn": p["funding_account_overdrawn"],
                "resolves": p["status"] == "within_reserve", "financing_cost_minor": cost, "dependencies": deps,
                "minimum_by_path": {x["key"]: x["minimum"]["amount_minor"] for x in res["paths"]},
                "paths_still_short": [x["key"] for x in res["paths"] if x["status"] == "shortfall"], "improvement_minor": p["minimum"]["amount_minor"] - bp["minimum"]["amount_minor"]}

    titles = {"collect": "Collect an overdue invoice", "defer": "Move a discretionary bill", "financing": "Use a financing match"}
    options = [evaluate(k, titles[k], [k]) for k in kinds]
    if len(kinds) >= 2:
        options.append(evaluate("combination", "Combination: " + " + ".join({"collect": "collect", "defer": "move bill", "financing": "financing"}[k] for k in kinds), kinds))

    qualifying = [o for o in options if o["resolves"]]
    qualifying.sort(key=lambda o: (o["borrows"], len(o["dependencies"]), o["financing_cost_minor"], o["effect_date"], len(o["actions"]), o["id"]))
    if blockers:
        state, rec, why = "none_safe", None, ["Material evidence in this review is still unverified, so no option can be called safe until it is verified."]
    elif not qualifying:
        closest = min(options, key=lambda o: (o["remaining_shortfall_minor"], o["borrows"], o["id"])) if options else None
        state, rec = "none_safe", None
        why = ["No option closes the shortfall on the chosen scenario." + (f" The closest, \"{closest['title']}\", still leaves {closest['remaining_shortfall_minor']} minor units short." if closest else " No option is available.")]
    else:
        state, rec = "recommended", qualifying[0]["id"]
        top = qualifying[0]
        why = [f"\"{top['title']}\" leaves no shortfall on the chosen scenario (lowest cash {top['minimum']['amount_minor']} minor units on {top['minimum']['date']}, reserve {floor}).",
               "It does not borrow." if not top["borrows"] else f"It borrows, at a total financing cost of {top['financing_cost_minor']} minor units; no option without borrowing closes the gap.",
               f"It has {len(top['dependencies'])} unresolved dependenc{'y' if len(top['dependencies']) == 1 else 'ies'}."]
        for o in qualifying[1:]:
            why.append(f"\"{o['title']}\" also closes the gap but is ranked lower: " + ("it borrows" if o["borrows"] and not top["borrows"] else f"it has {len(o['dependencies'])} dependencies, cost {o['financing_cost_minor']}") + ".")
    for o in options:
        o["recommended"] = o["id"] == rec
        o["note"] = ("Closes the shortfall." if o["resolves"] else f"Leaves {o['remaining_shortfall_minor']} minor units short on this scenario." if o["remaining_shortfall_minor"] else "Does not close the gap (the funding account would be overdrawn).")
    return {"calc_version": CALC_VERSION, "currency": inputs["currency"], "as_of": inputs["outlook"]["as_of"], "reserve_minor": floor, "target_path": target,
            "target_label": next(l for k, l in SCENARIOS if k == target),
            "baseline": {"minimum": bp["minimum"], "first_breach": bp["first_breach"], "remaining_shortfall_minor": bp["shortfall_minor"],
                         "minimum_by_path": {x["key"]: (x["minimum"]["amount_minor"] if x["minimum"] else None) for x in base["paths"]}, "decision": base["decision"]},
            "assumptions": eff, "options": options, "unavailable": [{"id": k, "reason": r} for k, r in unavailable.items()],
            "recommendation": {"state": state, "option_id": rec, "reasons": why}, "evidence_blockers": blockers,
            "notice": "These are proposals only. Nothing was changed in the ledger, any invoice or bill, or any external system, and nothing was sent to a lender or supplier."}

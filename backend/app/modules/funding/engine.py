"""Funding Matches engine, calculation version funding-v1. Pure: frozen inputs + a versioned catalogue in, matches out.

1. Hard filters, applied to every product, ALL reasons kept: jurisdiction, currency, amount range,
   business age (owner-declared months trading), revenue (average monthly operating receipts over the
   last 90 days from classified records), purpose, security, required documents (owner-declared),
   and funding time (the product's typical days to funds must fit before the date funds are needed).
   Security "receivables" is checked against the business's own records: the advance may not exceed
   the advance rate times its outstanding, undisputed receivables. Guarantor and collateral must be
   declared. A product failing any filter is EXCLUDED with every reason; it is never ranked.
2. For each eligible product, exact terms in integer minor units (rounding half up; any remainder goes
   to the last payment): processing fee (deducted from the proceeds), interest or fee, repayment dates,
   total repayment and total cost (fees + interest).
3. The product's money is inserted into the existing Cash Outlook (the unmodified outlook-v1 engine):
   net proceeds on the disbursement date (application assumed today + the product's funding days), and
   each repayment as an outflow on its date, into the chosen receiving account. Lowest cash and first
   reserve breach are compared with the baseline on both the expected and cautious paths. Only repayments
   inside the 13-week forecast can affect these figures; later ones are listed in the schedule.
4. Rank eligible products: cautious path stays above the reserve first, then higher lowest cash on the
   cautious path, then lower total cost, then faster funding, then product id. Reasons are returned.

Every result is illustrative. It is a potential match, never a lender approval, quote, or offer, and no
business data is sent anywhere.
"""

import calendar
import copy
from datetime import date, timedelta

from app.modules.outlook.engine import compute

CALC_VERSION = "funding-v1"
REVENUE_WINDOW_DAYS = 90
PURPOSES = ("working_capital", "inventory", "invoice_gap", "equipment", "other")
DOCUMENTS = ("bank_statements", "id", "trade_licence", "invoices", "wallet_history", "tax_certificate")
SECURITY_DECLARED = ("guarantor", "collateral")
LABEL = "Illustrative · potential match, not an approval"
NOTICE = ("Illustrative sample terms only. A match is not a lender approval, quote, or offer, and no business data was sent to any lender.")


class MatchError(ValueError):
    """The request cannot be modelled on these inputs (shown to the owner as a 422)."""


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _round_div(a: int, b: int) -> int:
    return (2 * a + b) // (2 * b)  # half up, integers only


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def validate(inputs: dict, req: dict) -> dict:
    as_of = _d(inputs["as_of"])
    cur = req["currency"]
    acc = next((a for a in inputs["outlook"]["accounts"] if a["id"] == req["receiving_account_id"]), None)
    if acc is None:
        raise MatchError("The receiving account is not an account of this business.")
    if acc["currency"] != cur:
        raise MatchError("The receiving account must be in the currency of the funding need.")
    if acc["usable_minor"] is None:
        raise MatchError("The receiving account has no verified balance, so the forecast cannot include the funds. Enter a balance anchor first.")
    if not as_of < _d(req["needed_by"]) <= as_of + timedelta(days=91):
        raise MatchError(f"The date funds are needed must be after {inputs['as_of']} and within the 13-week forecast.")
    return acc


def _schedule(p: dict, principal: int, disb: date) -> dict:
    pr = p["pricing"]
    fee = _round_div(principal * pr.get("processing_fee_bps", 0), 10000)
    rows: list[dict] = []
    interest = 0
    if p["kind"] == "term_loan":
        n = pr["installments"]
        interest = _round_div(principal * pr["interest_annual_bps"] * n, 10000 * 12)
        total, base = principal + interest, (principal + interest) // n
        for k in range(1, n + 1):
            rows.append({"date": add_months(disb, k).isoformat(), "amount_minor": base + (total - base * n if k == n else 0), "kind": "installment"})
    elif p["kind"] == "credit_line":
        days = pr["tenor_days"]
        interest = _round_div(principal * pr["interest_annual_bps"] * days, 10000 * 365)
        rows.append({"date": (disb + timedelta(days=days)).isoformat(), "amount_minor": principal + interest, "kind": "repayment"})
    else:  # invoice_advance
        days = pr["tenor_days"]
        periods = -(-days // 30)
        interest = _round_div(principal * pr["fee_per_30_days_bps"] * periods, 10000)
        rows.append({"date": (disb + timedelta(days=days)).isoformat(), "amount_minor": principal + interest, "kind": "repayment"})
    total_repay = sum(r["amount_minor"] for r in rows)
    return {"principal_minor": principal, "processing_fee_minor": fee, "net_proceeds_minor": principal - fee,
            "interest_or_fee_minor": interest, "total_repayment_minor": total_repay, "total_cost_minor": fee + interest,
            "cost_bps_of_principal": _round_div((fee + interest) * 10000, principal), "disbursement_date": disb.isoformat(), "schedule": rows}


def _filters(p: dict, inputs: dict, req: dict) -> list[dict]:
    as_of, cur, amount = _d(inputs["as_of"]), req["currency"], req["amount_minor"]
    out = []

    def check(code, ok, ok_msg, bad_msg):
        out.append({"code": code, "passed": ok, "message": ok_msg if ok else bad_msg})

    check("jurisdiction", inputs["jurisdiction"] in p["jurisdictions"], f"Offered in {inputs['jurisdiction']}.", f"Not offered in {inputs['jurisdiction']} (offered in {', '.join(p['jurisdictions'])}).")
    check("currency", cur in p["currencies"], f"Lends in {cur}.", f"Does not lend in {cur} (only {', '.join(p['currencies'])}).")
    check("amount", p["min_amount_minor"] <= amount <= p["max_amount_minor"], "The amount is within the product's range.",
          f"The amount is {'below the minimum' if amount < p['min_amount_minor'] else 'above the maximum'} of {p['min_amount_minor']} to {p['max_amount_minor']} minor units.")
    check("business_age", req["months_trading"] >= p["min_months_trading"], f"{req['months_trading']} months trading (declared) meets the {p['min_months_trading']}-month minimum.",
          f"Needs at least {p['min_months_trading']} months trading; {req['months_trading']} declared.")
    monthly = inputs["revenue_90d_minor"] // 3
    check("revenue", monthly >= p["min_monthly_revenue_minor"], "Average monthly operating receipts meet the minimum.",
          f"Needs average monthly operating receipts of {p['min_monthly_revenue_minor']} minor units; records show {monthly} over the last {REVENUE_WINDOW_DAYS} days.")
    check("purpose", req["purpose"] in p["purposes"], "The purpose is supported.", f"Does not support the purpose \"{req['purpose']}\" (supports {', '.join(p['purposes'])}).")
    sec = p["security"]
    if sec == "none":
        check("security", True, "No security required.", "")
    elif sec == "receivables":
        cap = inputs["receivables_eligible_minor"] * p["advance_rate_bps"] // 10000
        check("security", amount <= cap, "Backed by your outstanding, undisputed invoices.",
              f"Advances up to {p['advance_rate_bps'] // 100}% of outstanding, undisputed invoices ({cap} minor units available); {amount} requested.")
    else:
        check("security", sec in req["security_available"], f"The required {sec} is declared as available.", f"Requires a {sec}, which was not declared as available.")
    missing = [d for d in p["documents"] if d not in req["documents_available"]]
    check("documents", not missing, "All required documents are declared as available.", f"Missing documents: {', '.join(missing)}.")
    days = (_d(req["needed_by"]) - as_of).days
    check("funding_time", p["funding_days"] <= days, f"Typically funds in {p['funding_days']} day(s); needed in {days}.", f"Typically takes {p['funding_days']} day(s) to fund; funds are needed in {days}.")
    return out


def _cash_view(res: dict, cur: str) -> dict:
    c = next((x for x in res["currencies"] if x["currency"] == cur), None)
    if c is None:
        return {}
    return {p: {"lowest": c["lowest"][p], "first_breach": c["first_breach"][p]} for p in ("expected", "cautious")} | {"reserve_minor": c["reserve_minor"]}


def with_product(inputs: dict, req: dict, terms: dict, name: str) -> dict:
    """The existing forecast with this product's proceeds and repayments added, recomputed by the unmodified outlook engine."""
    changed = copy.deepcopy(inputs["outlook"])
    acc_id = req["receiving_account_id"]
    disb = _d(terms["disbursement_date"])
    next(a for a in changed["accounts"] if a["id"] == acc_id)["restriction_releases"].append(
        {"id": "financing-proceeds", "amount_minor": terms["net_proceeds_minor"], "reason": f"{name} proceeds", "effective_to": (disb - timedelta(days=1)).isoformat()})
    for k, r in enumerate(terms["schedule"], 1):
        changed["obligations"].append({"id": f"financing-repayment-{k}", "direction": "payable", "party": name, "document_number": f"repayment {k}",
                                       "due_date": r["date"], "outstanding_minor": r["amount_minor"], "currency": req["currency"], "disputed": False,
                                       "settlement_account_id": acc_id})
    return _cash_view(compute(changed), req["currency"])


def match(inputs: dict, catalogue: dict, req: dict) -> dict:
    validate(inputs, req)
    as_of = _d(inputs["as_of"])
    cur = req["currency"]
    baseline = _cash_view(compute(inputs["outlook"]), cur)
    eligible, excluded = [], []
    for p in catalogue["products"]:
        filters = _filters(p, inputs, req)
        base = {"id": p["id"], "name": p["name"], "kind": p["kind"], "provider": p["provider"], "label": LABEL, "repayment_rule": p["repayment"],
                "source": p["source"] | {"catalogue_version": catalogue["version"]}, "filters": filters, "funding_days": p["funding_days"]}
        if not all(f["passed"] for f in filters):
            excluded.append(base | {"reasons": [f["message"] for f in filters if not f["passed"]]})
            continue
        disb = max(as_of + timedelta(days=p["funding_days"]), as_of + timedelta(days=1))
        terms = _schedule(p, req["amount_minor"], disb)
        eligible.append(base | {"terms": terms, "forecast": with_product(inputs, req, terms, p["name"])})

    def lowest(x, path):
        return x["forecast"][path]["lowest"]["amount_minor"]

    eligible.sort(key=lambda x: (x["forecast"]["cautious"]["first_breach"] is not None, -lowest(x, "cautious"), x["terms"]["total_cost_minor"], x["funding_days"], x["id"]))
    cheapest = min((x["terms"]["total_cost_minor"] for x in eligible), default=None)
    fastest = min((x["funding_days"] for x in eligible), default=None)
    for i, x in enumerate(eligible, 1):
        f = x["forecast"]["cautious"]
        reserve = x["forecast"]["reserve_minor"]
        x["rank"] = i
        x["badges"] = ([("best_repayment_fit", "Best repayment fit")] if i == 1 else []) + \
                      ([("fastest_funding", "Fastest funding")] if x["funding_days"] == fastest else []) + \
                      ([("lowest_cost", "Lowest quoted cost")] if x["terms"]["total_cost_minor"] == cheapest else [])
        x["badges"] = [{"code": c, "label": l} for c, l in x["badges"]]
        x["rank_reasons"] = [
            (f"On the cautious path the lowest cash stays at {f['lowest']['amount_minor']} minor units on {f['lowest']['date']}, above the reserve of {reserve}."
             if f["first_breach"] is None else
             f"On the cautious path cash is still below the reserve from {f['first_breach']['date']} (lowest {f['lowest']['amount_minor']} minor units)."),
            f"Total cost {x['terms']['total_cost_minor']} minor units ({x['terms']['cost_bps_of_principal'] / 100:g}% of the amount); total repayment {x['terms']['total_repayment_minor']}.",
            f"Typically funds in {x['funding_days']} day(s); needed by {req['needed_by']}.",
        ]
    return {"calc_version": CALC_VERSION, "catalogue_version": catalogue["version"], "catalogue_updated": catalogue["updated"],
            "catalogue_source": catalogue["source"], "region_pack": catalogue.get("region_pack"), "as_of": inputs["as_of"], "currency": cur, "label": LABEL, "notice": NOTICE,
            "lender_approval": False, "data_shared": False, "baseline": baseline,
            "need": {"amount_minor": req["amount_minor"], "needed_by": req["needed_by"], "purpose": req["purpose"],
                     "receiving_account_id": req["receiving_account_id"]},
            "eligible": eligible, "excluded": excluded,
            "ranking_rule": "Cautious path above the reserve first; then higher lowest cash on the cautious path; then lower total cost; then faster funding."}

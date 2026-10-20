"""Smart Alerts engine, rules version alerts-v1. Pure: frozen inputs (JSON) in, alert candidates out.

Four deterministic rules. Each candidate carries a dedup key, so the same condition found again on a
later run is the same alert, never a new one.

reserve_breach     the Cash Outlook (recomputed from the frozen outlook inputs) closes below the owner reserve
                   on some day. high if the expected path breaches, medium if only the cautious path does.
overdue_invoice    an outstanding customer invoice is past its due date and not disputed (a dispute is a
                   different conversation). low 1-7 days, medium 8-30, high over 30.
unusual_outflow    a posted outflow in the last 7 days is at least 3x the median of the previous 90 days of
                   outflows in the same currency (needs 5 or more baseline outflows). Own-account transfers
                   and payments already allocated to an invoice/bill are not outflows of this kind.
                   medium, high at 10x or more.
repayment_pressure an outstanding loan-repayment bill due within 30 days (or already overdue) whose due day
                   closes below the reserve after paying it (the outlook already contains the payment).
                   high if overdue or the expected path is below, medium if only the cautious path is.

Amounts are integer minor units. Cause texts carry no money amounts; the amount is its own field.
"""

from datetime import date, timedelta

from app.modules.outlook.engine import compute

RULES_VERSION = "alerts-v1"
OUTFLOW_RECENT_DAYS = 7
OUTFLOW_BASELINE_DAYS = 90
OUTFLOW_MULTIPLE = 3
OUTFLOW_MIN_BASELINE = 5
REPAYMENT_HORIZON_DAYS = 30
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _nice_date(iso: str) -> str:
    d = _d(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def median(values: list[int]) -> int:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) // 2


def _reserve_breaches(inputs: dict, outlook: dict) -> list[dict]:
    out = []
    events = outlook["events"]
    for cur in outlook["currencies"]:
        breach = cur["first_breach"]
        if cur["reserve_minor"] is None or not (breach["expected"] or breach["cautious"]):
            continue
        primary = breach["expected"] or breach["cautious"]
        first = min(b["date"] for b in breach.values() if b)
        refs = [{"kind": "account", "id": a["id"]} for a in outlook["accounts"] if a["currency"] == cur["currency"]]
        for e in events:
            dates = [x for x in (e["expected_date"], e["cautious_date"]) if x]
            if e["currency"] == cur["currency"] and dates and min(dates) <= first and e["source"] == "obligation":
                refs.append({"kind": "obligation", "id": e["source_id"]})
        if breach["expected"]:
            cause = f"The expected path closes below the owner reserve on {_nice_date(breach['expected']['date'])}."
        else:
            cause = (f"Only the cautious path (receipts {outlook['rules']['cautious_delay_days']} days late, stale balances not relied on) "
                     f"closes below the owner reserve, on {_nice_date(breach['cautious']['date'])}.")
        out.append({"key": f"reserve_breach:{cur['currency']}", "type": "reserve_breach",
                    "severity": "high" if breach["expected"] else "medium", "alert_date": first,
                    "amount_minor": primary["gap_minor"], "currency": cur["currency"], "cause": cause,
                    "record_ids": refs,
                    "details": {"reserve_minor": cur["reserve_minor"],
                                "expected": breach["expected"], "cautious": breach["cautious"]}})
    return out


def _overdue_invoices(inputs: dict) -> list[dict]:
    out = []
    for o in inputs["obligations"]:
        if o["direction"] != "receivable" or o["disputed"] or o["status"] not in ("open", "partially_settled") or o["days_overdue"] <= 0:
            continue
        days = o["days_overdue"]
        out.append({"key": f"overdue_invoice:{o['id']}", "type": "overdue_invoice",
                    "severity": "high" if days > 30 else "medium" if days > 7 else "low",
                    "alert_date": o["due_date"], "amount_minor": o["outstanding_minor"], "currency": o["currency"],
                    "cause": f"{o['party']['name']} invoice {o['document_number']} was due {_nice_date(o['due_date'])} and is {days} day(s) overdue.",
                    "record_ids": [{"kind": "obligation", "id": o["id"]}],
                    "details": {"days_overdue": days, "party": o["party"]["name"], "document_number": o["document_number"]}})
    return out


def _unusual_outflows(inputs: dict) -> list[dict]:
    as_of = _d(inputs["as_of"])
    recent_from = as_of - timedelta(days=OUTFLOW_RECENT_DAYS - 1)
    out = []
    for cur in sorted({t["currency"] for t in inputs["outflows"]}):
        txns = [t for t in inputs["outflows"] if t["currency"] == cur]
        baseline = [t["amount_minor"] for t in txns if _d(t["business_date"]) < recent_from]
        if len(baseline) < OUTFLOW_MIN_BASELINE:
            continue
        med = median(baseline)
        for t in txns:
            if _d(t["business_date"]) < recent_from or t["amount_minor"] < OUTFLOW_MULTIPLE * med:
                continue
            tenths = t["amount_minor"] * 10 // med
            out.append({"key": f"unusual_outflow:{t['id']}", "type": "unusual_outflow",
                        "severity": "high" if tenths >= 100 else "medium",
                        "alert_date": t["business_date"], "amount_minor": t["amount_minor"], "currency": cur,
                        "cause": (f"Outflow \"{t['description']}\" is {tenths // 10}.{tenths % 10} times the median of "
                                  f"{len(baseline)} earlier outflows in {cur} over the previous {OUTFLOW_BASELINE_DAYS} days."),
                        "record_ids": [{"kind": "ledger_transaction", "id": t["id"]}, {"kind": "account", "id": t["account_id"]}],
                        "details": {"median_minor": med, "baseline_count": len(baseline), "multiple_tenths": tenths}})
    return out


def _repayment_pressure(inputs: dict, outlook: dict) -> list[dict]:
    as_of = _d(inputs["as_of"])
    tomorrow = as_of + timedelta(days=1)
    by_cur = {c["currency"]: c for c in outlook["currencies"]}
    out = []
    for o in inputs["obligations"]:
        if o["direction"] != "payable" or o["category"] != "loan_repayment" or o["status"] not in ("open", "partially_settled"):
            continue
        due = _d(o["due_date"])
        if due > as_of + timedelta(days=REPAYMENT_HORIZON_DAYS):
            continue
        label = f"{o['party']['name']} repayment {o['document_number']}"
        base = {"key": f"repayment_pressure:{o['id']}", "type": "repayment_pressure", "alert_date": o["due_date"],
                "amount_minor": o["outstanding_minor"], "currency": o["currency"],
                "record_ids": [{"kind": "obligation", "id": o["id"]}]}
        if due < as_of:
            out.append({**base, "severity": "high", "cause": f"{label} was due {_nice_date(o['due_date'])} and is {(as_of - due).days} day(s) overdue.",
                        "details": {"overdue": True}})
            continue
        cur = by_cur.get(o["currency"])
        if cur is None:
            continue
        day = cur["daily"][(max(due, tomorrow) - as_of).days - 1]
        floor = cur["reserve_minor"] or 0
        below = {"expected": day["expected_minor"] < floor, "cautious": day["cautious_minor"] < floor}
        if not (below["expected"] or below["cautious"]):
            continue
        which = "expected" if below["expected"] else "cautious"
        out.append({**base, "severity": "high" if below["expected"] else "medium",
                    "cause": (f"{label} is due {_nice_date(o['due_date'])}; after paying it the {which} path closes below the "
                              f"{'owner reserve' if cur['reserve_minor'] is not None else 'zero line'} that day."),
                    "details": {"overdue": False, "floor_minor": floor, "balance_expected_minor": day["expected_minor"],
                                "balance_cautious_minor": day["cautious_minor"], "balance_date": day["date"]}})
    return out


def evaluate(inputs: dict) -> list[dict]:
    outlook = compute(inputs["outlook"])
    found = (_reserve_breaches(inputs, outlook) + _overdue_invoices(inputs) + _unusual_outflows(inputs)
             + _repayment_pressure(inputs, outlook))
    return sorted(found, key=lambda a: (SEVERITY_ORDER[a["severity"]], a["alert_date"], a["key"]))

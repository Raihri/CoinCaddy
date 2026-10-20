"""Cash Outlook engine, calculation version outlook-v1. Pure: frozen inputs (JSON) in, paths out.

Paths are computed per account, per day, for HORIZON_DAYS days after the as-of date, and summed per
currency (currencies are never combined). Two paths:

expected
  opening  = usable balance (verified - restricted) + posted movements after the verified date, for
             every account that has a balance (fresh or stale; stale is reported as evidence).
  receipts = outstanding, non-disputed receivables on max(due date, tomorrow); overdue receivables are
             assumed collected OVERDUE_ASSUMED_DAYS after the as-of date and marked uncertain.
cautious
  opening  = as expected for fresh accounts; a stale account contributes only a negative balance.
  receipts = non-overdue, non-disputed receivables CAUTIOUS_DELAY_DAYS after the expected date;
             overdue receivables are left out.
both
  payments = outstanding payables (disputed included) on max(due date, tomorrow).
  restricted funds that end inside the horizon are released the day after their end date.
Accounts without any balance are excluded and reported as missing evidence. Only outstanding
amounts are used, so a paid invoice or an already-posted movement is never counted twice, and no
future own-account transfer is modelled, so a transfer cannot be counted on both accounts.
A day's figure is the closing balance after all of that day's movements.
"""

from collections import defaultdict
from datetime import date, timedelta

CALC_VERSION = "outlook-v1"
HORIZON_DAYS = 91          # 13 weeks
DAILY_DAYS = 30
CAUTIOUS_DELAY_DAYS = 14
OVERDUE_ASSUMED_DAYS = 7


def _d(s: str) -> date:
    return date.fromisoformat(s)


def build_events(inputs: dict) -> tuple[list[dict], list[dict]]:
    """Dated movements for each path, plus evidence notes. Every event names its source record."""
    as_of = _d(inputs["as_of"])
    tomorrow = as_of + timedelta(days=1)
    accounts = {a["id"]: a for a in inputs["accounts"]}
    default_account: dict[str, str] = {}
    for a in inputs["accounts"]:  # first account per currency with a balance, else the first account at all
        cur = default_account.get(a["currency"])
        if cur is None or (accounts[cur]["usable_minor"] is None and a["usable_minor"] is not None):
            default_account[a["currency"]] = a["id"]
    events, evidence = [], []
    for o in inputs["obligations"]:
        acc_id = o["settlement_account_id"] if o["settlement_account_id"] in accounts else default_account.get(o["currency"])
        base = {"source": "obligation", "source_id": o["id"], "label": f"{o['party']} {o['document_number']}",
                "direction": o["direction"], "currency": o["currency"], "account_id": acc_id,
                "assumed_account": o["settlement_account_id"] not in accounts}
        if acc_id is None:
            evidence.append({"code": "no_account_in_currency", "severity": "warning", "source_id": o["id"],
                             "message": f"{base['label']} is in {o['currency']} but no account uses that currency; it is left out."})
            continue
        if base["assumed_account"]:
            evidence.append({"code": "assumed_account", "severity": "info", "source_id": o["id"],
                             "message": f"{base['label']} has no expected account; {accounts[acc_id]['label']} is assumed."})
        due = _d(o["due_date"])
        if o["direction"] == "payable":
            when = max(due, tomorrow)
            events.append({**base, "amount_minor": -o["outstanding_minor"], "expected_date": when.isoformat(), "cautious_date": when.isoformat(),
                           "uncertain": False,
                           "note": "Overdue: assumed paid tomorrow" if due < tomorrow else ("Disputed, still counted" if o["disputed"] else None)})
            continue
        if o["disputed"]:
            events.append({**base, "amount_minor": o["outstanding_minor"], "expected_date": None, "cautious_date": None,
                           "uncertain": True, "note": "Disputed: left out of both paths"})
            evidence.append({"code": "disputed_receivable", "severity": "info", "source_id": o["id"],
                             "message": f"{base['label']} is disputed and is not counted as incoming cash."})
        elif due < as_of:
            when = as_of + timedelta(days=OVERDUE_ASSUMED_DAYS)
            events.append({**base, "amount_minor": o["outstanding_minor"], "expected_date": when.isoformat(), "cautious_date": None,
                           "uncertain": True,
                           "note": f"Overdue {(as_of - due).days} days: expected path assumes collection in {OVERDUE_ASSUMED_DAYS} days; cautious path leaves it out"})
        else:
            when = max(due, tomorrow)
            events.append({**base, "amount_minor": o["outstanding_minor"], "expected_date": when.isoformat(),
                           "cautious_date": (when + timedelta(days=CAUTIOUS_DELAY_DAYS)).isoformat(),
                           "uncertain": False, "note": f"Cautious path assumes {CAUTIOUS_DELAY_DAYS} days late"})
    for a in inputs["accounts"]:
        for r in a.get("restriction_releases", []):
            when = _d(r["effective_to"]) + timedelta(days=1)
            if as_of < when <= as_of + timedelta(days=HORIZON_DAYS):
                events.append({"source": "restricted_fund", "source_id": r["id"], "label": f"Restriction ends: {r['reason']}",
                               "direction": "release", "currency": a["currency"], "account_id": a["id"], "assumed_account": False,
                               "amount_minor": r["amount_minor"], "expected_date": when.isoformat(), "cautious_date": when.isoformat(),
                               "uncertain": False, "note": None})
    return events, evidence


def opening(a: dict) -> tuple[int, int] | None:
    if a["usable_minor"] is None:
        return None
    value = a["usable_minor"] + a["uncovered_minor"]
    return value, (value if a["freshness"] == "fresh" else min(0, value))


def compute(inputs: dict) -> dict:
    as_of = _d(inputs["as_of"])
    days = [as_of + timedelta(days=i) for i in range(1, HORIZON_DAYS + 1)]
    events, evidence = build_events(inputs)
    for a in inputs["accounts"]:
        if a["usable_minor"] is None:
            evidence.append({"code": "missing_balance", "severity": "warning", "account_id": a["id"],
                             "message": f"{a['label']} has no verified balance and is left out of both paths."})
        elif a["freshness"] == "stale":
            evidence.append({"code": "stale_balance", "severity": "warning", "account_id": a["id"],
                             "message": f"{a['label']} is verified only through {a['evidence_through']}; the expected path uses it, the cautious path does not."})
        if a["usable_minor"] is not None and a["uncovered_count"]:
            evidence.append({"code": "uncovered_movements", "severity": "info", "account_id": a["id"],
                             "message": f"{a['label']}: {a['uncovered_count']} posted movement(s) after the verified date are included but not yet backed by a statement period."})
    for t in inputs.get("unmatched_transfers", []):
        evidence.append({"code": "unmatched_transfer", "severity": "warning", "source_id": t["id"],
                         "message": "An own-account transfer has a missing side, so one account's balance may be incomplete."})

    # Per-account daily closing balances for both paths.
    move = {p: defaultdict(lambda: defaultdict(int)) for p in ("expected", "cautious")}
    for e in events:
        for p in ("expected", "cautious"):
            if e[f"{p}_date"]:
                move[p][e["account_id"]][e[f"{p}_date"]] += e["amount_minor"]
    accounts_out, by_currency = [], defaultdict(list)
    for a in inputs["accounts"]:
        op_ = opening(a)
        if op_ is None:
            continue
        paths = {}
        for p, start in zip(("expected", "cautious"), op_):
            bal, series = start, []
            for d in days:
                bal += move[p][a["id"]].get(d.isoformat(), 0)
                series.append(bal)
            paths[p] = series
        accounts_out.append({"id": a["id"], "label": a["label"], "currency": a["currency"], "freshness": a["freshness"],
                             "opening_expected_minor": op_[0], "opening_cautious_minor": op_[1],
                             "weekly_expected_minor": [paths["expected"][7 * k - 1] for k in range(1, 14)],
                             "weekly_cautious_minor": [paths["cautious"][7 * k - 1] for k in range(1, 14)],
                             "lowest_expected_minor": min(paths["expected"]), "lowest_cautious_minor": min(paths["cautious"])})
        by_currency[a["currency"]].append((op_, paths))

    currencies = []
    for cur, items in sorted(by_currency.items()):
        reserve = inputs["reserve"]["amount_minor"] if inputs["reserve"]["currency"] == cur else None
        total = {p: [sum(paths[p][i] for _, paths in items) for i in range(HORIZON_DAYS)] for p in ("expected", "cautious")}
        out = {"currency": cur, "reserve_minor": reserve,
               "opening_expected_minor": sum(o[0] for o, _ in items), "opening_cautious_minor": sum(o[1] for o, _ in items),
               "daily": [{"date": days[i].isoformat(), "expected_minor": total["expected"][i], "cautious_minor": total["cautious"][i]}
                         for i in range(DAILY_DAYS)],
               "weekly": [], "lowest": {}, "lowest_30d": {}, "first_breach": {}}
        for k in range(1, 14):
            window = slice(7 * (k - 1), 7 * k)
            out["weekly"].append({"week": k, "end_date": days[7 * k - 1].isoformat(),
                                  "expected_close_minor": total["expected"][7 * k - 1], "cautious_close_minor": total["cautious"][7 * k - 1],
                                  "expected_min_minor": min(total["expected"][window]), "cautious_min_minor": min(total["cautious"][window])})
        for p in ("expected", "cautious"):
            s = total[p]
            i = min(range(HORIZON_DAYS), key=lambda j: (s[j], j))
            out["lowest"][p] = {"date": days[i].isoformat(), "amount_minor": s[i]}
            i30 = min(range(DAILY_DAYS), key=lambda j: (s[j], j))
            out["lowest_30d"][p] = {"date": days[i30].isoformat(), "amount_minor": s[i30]}
            breach = next((j for j in range(HORIZON_DAYS) if reserve is not None and s[j] < reserve), None)
            out["first_breach"][p] = None if breach is None else {
                "date": days[breach].isoformat(), "balance_minor": s[breach], "gap_minor": reserve - s[breach]}
        currencies.append(out)

    order = {"warning": 0, "info": 1}
    return {
        "calc_version": CALC_VERSION, "as_of": inputs["as_of"], "horizon_days": HORIZON_DAYS,
        "rules": {"cautious_delay_days": CAUTIOUS_DELAY_DAYS, "overdue_assumed_days": OVERDUE_ASSUMED_DAYS},
        "currencies": currencies, "accounts": accounts_out,
        "events": sorted(events, key=lambda e: (e["expected_date"] or e["cautious_date"] or "9999", e["label"])),
        "evidence": sorted(evidence, key=lambda e: order[e["severity"]]),
    }

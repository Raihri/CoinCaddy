"""Independent self-check of the synthetic fixture's hand-checked expected values.

This deliberately re-derives totals with plain integer sums instead of product code, so the frozen
expected values can later be used to test the real ledger and analysis features.
"""

from collections import defaultdict
from datetime import datetime

from app.contracts.business_time import business_date
from app.fixtures.seed import load_fixture

FX = load_fixture()


def _amounts_are_ints(obj):
    if isinstance(obj, dict):
        if "amount_minor" in obj:
            assert type(obj["amount_minor"]) is int, obj
            assert obj["currency"] == "BDT", obj
        for v in obj.values():
            _amounts_are_ints(v)
    elif isinstance(obj, list):
        for v in obj:
            _amounts_are_ints(v)


def test_fixture_contract_fields():
    assert FX["synthetic"] is True
    assert FX["business"]["base_currency"] == "BDT"
    assert FX["business"]["timezone"] == "Asia/Dhaka"
    _amounts_are_ints(FX)


def test_business_dates_match_utc_events_in_business_timezone():
    tz = FX["business"]["timezone"]
    for t in FX["transactions"]:
        utc = datetime.fromisoformat(t["event_utc"].replace("Z", "+00:00"))
        assert business_date(utc, tz).isoformat() == t["business_date"], t["ref"]


def test_closing_balances_per_account():
    closing = {a["ref"]: a["opening"]["amount_minor"] for a in FX["accounts"]}
    for t in FX["transactions"]:
        closing[t["account"]] += t["amount_minor"]
    expected = {k: v["amount_minor"] for k, v in FX["expected"]["closing_by_account"].items()}
    assert closing == expected == {"ACC-BANK": 16224975, "ACC-WALLET": 6300035, "ACC-CASH": 850000}


def test_operating_totals_exclude_own_transfers():
    e = FX["expected"]
    receipts = sum(t["amount_minor"] for t in FX["transactions"] if t["kind"] == "operating_receipt")
    payments = -sum(t["amount_minor"] for t in FX["transactions"] if t["kind"] == "operating_payment")
    transfers = sum(t["amount_minor"] for t in FX["transactions"] if t["kind"] == "own_transfer")
    assert receipts == e["operating_receipts"]["amount_minor"] == 8349975
    assert payments == e["operating_payments"]["amount_minor"] == 8925015
    assert receipts - payments == e["net_operating_cash"]["amount_minor"] == -575040
    assert transfers == e["own_transfer_net"]["amount_minor"] == 0


def test_totals_and_lowest_end_of_day():
    e = FX["expected"]
    opening = sum(a["opening"]["amount_minor"] for a in FX["accounts"])
    assert opening == e["opening_total_same_currency"]["amount_minor"] == 23950050
    by_day = defaultdict(int)
    for t in FX["transactions"]:
        by_day[t["business_date"]] += t["amount_minor"]
    running, lowest = opening, (FX["period"]["start_business_date"], opening)
    for day in sorted(by_day):
        running += by_day[day]
        if running < lowest[1]:
            lowest = (day, running)
    assert running == e["closing_total_same_currency"]["amount_minor"] == 23375010
    assert lowest == (e["lowest_end_of_day_total"]["business_date"], e["lowest_end_of_day_total"]["total"]["amount_minor"])

"""Cash Outlook tests. Exact values: fixtures/cash_outlook_expected.md (pure engine) and API/snapshot behaviour."""

import copy
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import REPO_ROOT
from app.db import get_engine
from app.fixtures.seed import seed
from app.modules.outlook.engine import compute
from app.modules.records.service import business_today
from tests.helpers import Biz

CASE = json.loads((REPO_ROOT / "fixtures" / "cash_outlook_case.json").read_text(encoding="utf-8"))["inputs"]


@pytest.fixture(autouse=True)
def _seed():
    seed()


@pytest.fixture(scope="module")
def result():
    return compute(CASE)


def daily(res, day):
    return next(d for d in res["currencies"][0]["daily"] if d["date"] == day)


# --- exact paths from the hand-checked case ------------------------------------------------------------


def test_opening_cash_keeps_stale_out_of_the_cautious_path(result):
    bdt = result["currencies"][0]
    assert (bdt["currency"], bdt["reserve_minor"]) == ("BDT", 8_000_000)
    assert (bdt["opening_expected_minor"], bdt["opening_cautious_minor"]) == (11_000_000, 10_000_000)
    assert [a["label"] for a in result["accounts"]] == ["Bank", "Wallet"]  # Cash (no balance) left out
    wallet = result["accounts"][1]
    assert (wallet["opening_expected_minor"], wallet["opening_cautious_minor"]) == (1_000_000, 0)


def test_on_time_late_and_missing_receipts(result):
    ev = {e["source_id"]: e for e in result["events"]}
    assert (ev["R1"]["expected_date"], ev["R1"]["cautious_date"], ev["R1"]["uncertain"]) == ("2026-10-25", "2026-11-08", False)
    assert (ev["R2"]["expected_date"], ev["R2"]["cautious_date"], ev["R2"]["uncertain"]) == ("2026-10-27", None, True)
    assert (ev["R3"]["expected_date"], ev["R3"]["cautious_date"], ev["R3"]["uncertain"]) == (None, None, True)
    assert (ev["P2"]["expected_date"], ev["P2"]["amount_minor"], ev["P2"]["assumed_account"]) == ("2026-10-21", -1_500_000, True)
    assert ev["P1"]["cautious_date"] == "2026-10-27"
    assert ev["rf-1"]["expected_date"] == "2026-11-15"


def test_exact_daily_paths(result):
    expected = {"2026-10-21": (9_500_000, 8_500_000), "2026-10-24": (9_500_000, 8_500_000),
                "2026-10-25": (14_500_000, 8_500_000), "2026-10-27": (10_500_000, 1_500_000),
                "2026-11-07": (10_500_000, 1_500_000), "2026-11-08": (10_500_000, 6_500_000),
                "2026-11-19": (11_000_000, 7_000_000)}
    for day, (exp, caut) in expected.items():
        d = daily(result, day)
        assert (d["expected_minor"], d["cautious_minor"]) == (exp, caut), day
    assert len(result["currencies"][0]["daily"]) == 30 and result["currencies"][0]["daily"][0]["date"] == "2026-10-21"


def test_exact_weekly_paths(result):
    weeks = result["currencies"][0]["weekly"]
    assert len(weeks) == 13
    rows = [(w["week"], w["end_date"], w["expected_close_minor"], w["expected_min_minor"], w["cautious_close_minor"], w["cautious_min_minor"])
            for w in weeks]
    assert rows[0] == (1, "2026-10-27", 10_500_000, 9_500_000, 1_500_000, 1_500_000)
    assert rows[1] == (2, "2026-11-03", 10_500_000, 10_500_000, 1_500_000, 1_500_000)
    assert rows[2] == (3, "2026-11-10", 10_500_000, 10_500_000, 6_500_000, 1_500_000)
    assert rows[3] == (4, "2026-11-17", 11_000_000, 10_500_000, 7_000_000, 6_500_000)
    assert rows[12] == (13, "2027-01-19", 11_000_000, 11_000_000, 7_000_000, 7_000_000)


def test_lowest_and_reserve_breach(result):
    bdt = result["currencies"][0]
    assert bdt["lowest"]["expected"] == {"date": "2026-10-21", "amount_minor": 9_500_000}
    assert bdt["lowest"]["cautious"] == {"date": "2026-10-27", "amount_minor": 1_500_000}
    assert bdt["first_breach"]["expected"] is None
    assert bdt["first_breach"]["cautious"] == {"date": "2026-10-27", "balance_minor": 1_500_000, "gap_minor": 6_500_000}


def test_per_account_paths_and_evidence(result):
    bank = result["accounts"][0]
    assert bank["weekly_expected_minor"][:4] == [9_500_000, 9_500_000, 9_500_000, 10_000_000]
    assert (bank["lowest_expected_minor"], bank["lowest_cautious_minor"]) == (8_500_000, 1_500_000)
    codes = {(e["code"], e.get("account_id") or e.get("source_id")) for e in result["evidence"]}
    assert codes == {("stale_balance", "acc-wallet"), ("missing_balance", "acc-cash"),
                     ("disputed_receivable", "R3"), ("assumed_account", "P2")}
    assert result["calc_version"] == "outlook-v1"


def test_currencies_stay_separate_and_unknown_currency_is_reported():
    inputs = copy.deepcopy(CASE)
    inputs["accounts"].append({"id": "acc-usd", "label": "USD account", "currency": "USD", "usable_minor": 50_000, "freshness": "fresh",
                               "evidence_through": "2026-10-19", "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "x", "restriction_releases": []})
    inputs["obligations"].append({"id": "E1", "direction": "receivable", "party": "Euro Co", "document_number": "E-1", "due_date": "2026-10-30",
                                  "outstanding_minor": 1_000, "currency": "EUR", "disputed": False, "settlement_account_id": None})
    res = compute(inputs)
    by_cur = {c["currency"]: c for c in res["currencies"]}
    assert set(by_cur) == {"BDT", "USD"}
    assert by_cur["BDT"]["opening_expected_minor"] == 11_000_000  # USD never added in
    assert (by_cur["USD"]["reserve_minor"], by_cur["USD"]["first_breach"]["expected"]) == (None, None)
    assert any(e["code"] == "no_account_in_currency" and e["source_id"] == "E1" for e in res["evidence"])


def test_engine_is_deterministic(result):
    assert compute(copy.deepcopy(CASE)) == result


# --- API, snapshot, frozen inputs ------------------------------------------------------------------


def setup_business() -> Biz:
    """A business with a fresh bank balance and one receivable and one payable dated relative to today."""
    biz = Biz()
    today = business_today("Asia/Dhaka")
    bank = biz.account["ACC-BANK"]
    r = biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": today.isoformat(), "balance_minor": 9_000_000,
                                                        "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Today"})
    assert r.status_code == 201, r.text
    for direction, num, days, amount, cat in (("receivable", "INV-A", 5, 4_000_000, "sales"), ("payable", "BILL-A", 3, 2_000_000, "rent")):
        r = biz.owner.post(f"{biz.base}/obligations", json={
            "direction": direction, "party_name": "Party " + num, "document_number": num, "issue_date": today.isoformat(),
            "due_date": (today + timedelta(days=days)).isoformat(), "amount_minor": amount, "currency": "BDT", "category": cat,
            "source_reference": "book", "settlement_account_id": bank})
        assert r.status_code == 201, r.text
    biz.today = today
    return biz


def test_snapshot_freezes_inputs_and_reproduces():
    biz = setup_business()
    assert biz.owner.get(f"{biz.base}/cash-outlook/latest").status_code == 404
    r = biz.owner.post(f"{biz.base}/cash-outlook/snapshots")
    assert r.status_code == 201, r.text
    snap = r.json()
    t = biz.today
    bdt = snap["results"]["currencies"][0]
    # 9,000,000 opening; bill -2,000,000 on day 3; invoice +4,000,000 on day 5 (expected) / day 19 (cautious).
    assert bdt["opening_expected_minor"] == 9_000_000
    assert next(d for d in bdt["daily"] if d["date"] == (t + timedelta(days=3)).isoformat())["expected_minor"] == 7_000_000
    assert next(d for d in bdt["daily"] if d["date"] == (t + timedelta(days=5)).isoformat())["expected_minor"] == 11_000_000
    assert next(d for d in bdt["daily"] if d["date"] == (t + timedelta(days=5)).isoformat())["cautious_minor"] == 7_000_000
    assert bdt["first_breach"]["cautious"] == {"date": (t + timedelta(days=3)).isoformat(), "balance_minor": 7_000_000, "gap_minor": 1_000_000}
    assert snap["current"] == {"today": t.isoformat(), "is_today": True, "inputs_unchanged": True}
    v = biz.owner.get(f"{biz.base}/cash-outlook/snapshots/{snap['id']}/verify").json()
    assert v == {"snapshot_id": snap["id"], "calc_version": "outlook-v1", "inputs_hash_matches": True, "results_reproduce": True}
    # Every event links back to the record it came from.
    assert {e["label"] for e in snap["results"]["events"]} == {"Party INV-A INV-A", "Party BILL-A BILL-A"}
    assert all(e["source"] == "obligation" and e["source_id"] for e in snap["results"]["events"])


def test_snapshot_stays_frozen_when_records_change():
    biz = setup_business()
    snap = biz.owner.post(f"{biz.base}/cash-outlook/snapshots").json()
    inv = next(o for o in biz.owner.get(f"{biz.base}/obligations").json()["items"] if o["document_number"] == "INV-A")
    biz.owner.post(f"{biz.base}/obligations/{inv['id']}/allocations", json={"amount_minor": 4_000_000, "currency": "BDT",
                                                                            "allocation_date": biz.today.isoformat()})
    again = biz.owner.get(f"{biz.base}/cash-outlook/snapshots/{snap['id']}").json()
    assert again["results"] == snap["results"]                   # the stored figures did not move
    assert again["current"]["inputs_unchanged"] is False         # ...and the page is told inputs changed
    fresh = biz.owner.post(f"{biz.base}/cash-outlook/snapshots").json()
    assert all(e["source_id"] != inv["id"] for e in fresh["results"]["events"])  # paid invoice is no longer counted
    assert len(biz.owner.get(f"{biz.base}/cash-outlook/snapshots").json()) == 2


def test_only_the_outstanding_part_of_a_partly_paid_bill_is_counted():
    biz = setup_business()
    bill = next(o for o in biz.owner.get(f"{biz.base}/obligations").json()["items"] if o["document_number"] == "BILL-A")
    biz.owner.post(f"{biz.base}/obligations/{bill['id']}/allocations", json={"amount_minor": 500_000, "currency": "BDT",
                                                                             "allocation_date": biz.today.isoformat()})
    snap = biz.owner.post(f"{biz.base}/cash-outlook/snapshots").json()
    event = next(e for e in snap["results"]["events"] if e["source_id"] == bill["id"])
    assert event["amount_minor"] == -1_500_000  # only the outstanding part


def test_permissions_isolation_and_immutability():
    biz = setup_business()
    staff = biz.member("staff")
    assert staff.post(f"{biz.base}/cash-outlook/snapshots").status_code == 403
    snap = biz.member("bookkeeper").post(f"{biz.base}/cash-outlook/snapshots").json()
    assert staff.get(f"{biz.base}/cash-outlook/latest").json()["id"] == snap["id"]  # staff may read
    other = Biz()
    assert other.owner.get(f"{biz.base}/cash-outlook/latest").status_code == 404
    assert other.owner.get(f"{other.base}/cash-outlook/snapshots/{snap['id']}").status_code == 404
    assert other.owner.get(f"{other.base}/cash-outlook/snapshots/{snap['id']}/verify").status_code == 404
    for stmt in ("UPDATE forecast_snapshot SET results = '{}'::jsonb", "DELETE FROM forecast_snapshot"):
        with pytest.raises(DBAPIError, match="append-only"):
            with get_engine().begin() as conn:
                conn.execute(text(stmt + " WHERE business_id = :b"), {"b": biz.id})
    with get_engine().connect() as conn:
        events = conn.execute(text("SELECT count(*) FROM audit_event WHERE business_id = :b AND action = 'outlook.snapshot'"), {"b": biz.id}).scalar_one()
    assert events == 1


def test_missing_balance_is_reported_not_guessed():
    biz = Biz()
    snap = biz.owner.post(f"{biz.base}/cash-outlook/snapshots").json()
    assert snap["results"]["currencies"] == [] and snap["results"]["accounts"] == []
    assert {e["code"] for e in snap["results"]["evidence"]} == {"missing_balance"}
    assert len([e for e in snap["results"]["evidence"] if e["code"] == "missing_balance"]) == 3

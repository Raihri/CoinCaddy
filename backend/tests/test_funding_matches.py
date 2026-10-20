"""Funding Matches: hand-checked terms, filters, and forecast effect (pure engine), then the API."""

import copy
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import text

from app.db import get_engine
from app.modules.funding.engine import MatchError, add_months, match
from app.modules.funding.router import CATALOGUE
from app.modules.outlook.engine import compute
from app.modules.records.service import business_today
from tests.helpers import Biz
import pytest

AS_OF = "2026-10-20"
ALL_DOCS = ["bank_statements", "id", "trade_licence", "invoices", "wallet_history"]   # tax_certificate is NOT held


def _inputs(balance=10_000_000, revenue_90d=30_000_000, receivables=20_000_000, obligations=None):
    ob = obligations if obligations is not None else [{"id": "BILL-1", "direction": "payable", "party": "Landlord", "document_number": "RENT", "due_date": "2026-10-27",
                                                       "outstanding_minor": 5_000_000, "currency": "BDT", "disputed": False, "settlement_account_id": "bank"}]
    return {"as_of": AS_OF, "jurisdiction": "BD", "revenue_90d_minor": revenue_90d, "receivables_eligible_minor": receivables, "outlook": {
        "calc_version": "outlook-v1", "as_of": AS_OF, "base_currency": "BDT", "reserve": {"amount_minor": 8_000_000, "currency": "BDT"},
        "accounts": [{"id": "bank", "label": "Bank", "currency": "BDT", "usable_minor": balance, "freshness": "fresh", "evidence_through": AS_OF,
                      "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "an", "restriction_releases": []}],
        "obligations": ob, "unmatched_transfers": []}}


def _req(**kw):
    return {"amount_minor": 4_000_000, "currency": "BDT", "needed_by": "2026-10-27", "purpose": "working_capital", "receiving_account_id": "bank",
            "months_trading": 18, "security_available": ["guarantor"], "documents_available": ALL_DOCS} | kw


# Hand-worked case: bank 10,000,000, reserve 8,000,000, a 5,000,000 rent bill on 27 Oct, so the BASELINE already falls to
# 5,000,000 on 27 Oct (gap 3,000,000). The owner needs 4,000,000 by 27 Oct. 4,000,000 = BDT 40,000.
def prod(res, pid):
    return next(p for p in res["eligible"] if p["id"] == pid)


def test_exact_terms_for_each_eligible_product():
    res = match(_inputs(), CATALOGUE, _req())
    assert [p["id"] for p in res["eligible"]] == ["term-loan", "invoice-advance", "wc-line"]

    wc = prod(res, "wc-line")["terms"]      # fee 0.5% = 20,000; interest 18% x 60/365 = 118,356.16 -> 118,356; repaid 22 Dec
    assert (wc["processing_fee_minor"], wc["net_proceeds_minor"], wc["interest_or_fee_minor"]) == (20_000, 3_980_000, 118_356)
    assert (wc["total_repayment_minor"], wc["total_cost_minor"], wc["disbursement_date"]) == (4_118_356, 138_356, "2026-10-23")
    assert wc["schedule"] == [{"date": "2026-12-22", "amount_minor": 4_118_356, "kind": "repayment"}]

    adv = prod(res, "invoice-advance")["terms"]   # 1.5% per 30 days, 45 days = 2 periods = 120,000; repaid 5 Dec
    assert (adv["processing_fee_minor"], adv["interest_or_fee_minor"], adv["total_repayment_minor"], adv["total_cost_minor"]) == (0, 120_000, 4_120_000, 120_000)
    assert adv["schedule"] == [{"date": "2026-12-05", "amount_minor": 4_120_000, "kind": "repayment"}] and adv["disbursement_date"] == "2026-10-21"

    tl = prod(res, "term-loan")["terms"]    # 12% flat x 6 months = 240,000; 4,240,000 / 6 = 706,666 R4, remainder on the last
    assert (tl["interest_or_fee_minor"], tl["total_repayment_minor"], tl["total_cost_minor"], tl["disbursement_date"]) == (240_000, 4_240_000, 240_000, "2026-10-27")
    amounts = [r["amount_minor"] for r in tl["schedule"]]
    assert amounts == [706_666] * 5 + [706_670] and sum(amounts) == 4_240_000
    assert [r["date"] for r in tl["schedule"]] == ["2026-11-27", "2026-12-27", "2027-01-27", "2027-02-27", "2027-03-27", "2027-04-27"]
    assert tl["cost_bps_of_principal"] == 600


def test_two_products_with_different_repayment_effects_on_the_existing_forecast():
    res = match(_inputs(), CATALOGUE, _req())
    base = res["baseline"]       # the same figures the Cash Outlook engine gives
    direct = compute(_inputs()["outlook"])["currencies"][0]
    assert base["expected"]["lowest"] == direct["lowest"]["expected"] == {"date": "2026-10-27", "amount_minor": 5_000_000}
    assert base["cautious"]["first_breach"] == {"date": "2026-10-27", "balance_minor": 5_000_000, "gap_minor": 3_000_000}

    wc = prod(res, "wc-line")["forecast"]["cautious"]     # one big repayment on 22 Dec: 10,000,000 + 3,980,000 - 5,000,000 - 4,118,356
    assert wc["lowest"] == {"date": "2026-12-22", "amount_minor": 4_861_644}
    assert wc["first_breach"] == {"date": "2026-12-22", "balance_minor": 4_861_644, "gap_minor": 3_138_356}
    tl = prod(res, "term-loan")["forecast"]["cautious"]   # small monthly repayments: 10,000,000 + 4,000,000 - 5,000,000 - 2 x 706,666 by 27 Dec
    assert tl["lowest"] == {"date": "2026-12-27", "amount_minor": 7_586_668}
    assert tl["first_breach"] == {"date": "2026-12-27", "balance_minor": 7_586_668, "gap_minor": 413_332}
    adv = prod(res, "invoice-advance")["forecast"]["cautious"]
    assert adv["lowest"] == {"date": "2026-12-05", "amount_minor": 4_880_000}
    assert wc["lowest"] != tl["lowest"] != adv["lowest"]
    assert prod(res, "wc-line")["forecast"]["expected"]["lowest"] == wc["lowest"]      # no receipts: both paths agree
    assert prod(res, "term-loan")["forecast"]["reserve_minor"] == 8_000_000


def test_ranking_badges_and_visible_reasons():
    res = match(_inputs(), CATALOGUE, _req())
    assert [(p["rank"], p["id"]) for p in res["eligible"]] == [(1, "term-loan"), (2, "invoice-advance"), (3, "wc-line")]
    badges = {p["id"]: [b["code"] for b in p["badges"]] for p in res["eligible"]}
    assert badges == {"term-loan": ["best_repayment_fit"], "invoice-advance": ["fastest_funding", "lowest_cost"], "wc-line": []}
    reasons = prod(res, "term-loan")["rank_reasons"]
    assert len(reasons) == 3 and "7586668" in reasons[0] and "240000" in reasons[1] and "7 day(s)" in reasons[2]
    assert all(p["label"] == "Illustrative · potential match, not an approval" for p in res["eligible"] + res["excluded"])
    assert res["lender_approval"] is False and res["data_shared"] is False and "not a lender approval" in res["notice"]
    assert res["ranking_rule"].startswith("Cautious path above the reserve first")


def test_a_product_that_keeps_cash_above_the_reserve_outranks_one_that_does_not():
    # Bank 14,000,000: baseline 9,000,000 after the bill, so a 1,000,000 need keeps every product above the reserve.
    res = match(_inputs(balance=14_000_000), CATALOGUE, _req(amount_minor=1_000_000))
    assert all(p["forecast"]["cautious"]["first_breach"] is None for p in res["eligible"])
    # Bank 12,500,000 and a 2,000,000 need: the term loan's small monthly repayments leave 8,793,334 by 27 Dec (above the reserve); the
    # three single-repayment products fall to 7,440,000 (invoice advance), 7,430,822 (working capital line), and 7,380,822 (micro-loan).
    tight = match(_inputs(balance=12_500_000), CATALOGUE, _req(amount_minor=2_000_000))
    got = [(p["id"], p["forecast"]["cautious"]["lowest"]["amount_minor"], p["forecast"]["cautious"]["first_breach"] is not None) for p in tight["eligible"]]
    assert got == [("term-loan", 8_793_334, False), ("invoice-advance", 7_440_000, True), ("wc-line", 7_430_822, True), ("wallet-microloan", 7_380_822, True)]


def test_excluded_products_show_every_reason_and_their_source():
    res = match(_inputs(), CATALOGUE, _req())
    ex = {p["id"]: p for p in res["excluded"]}
    assert set(ex) == {"wallet-microloan", "secured-bank-loan", "ke-trade-line"}
    assert ex["wallet-microloan"]["reasons"] == ["The amount is above the maximum of 100000 to 3000000 minor units."]
    sec = ex["secured-bank-loan"]["reasons"]
    assert [f["code"] for f in ex["secured-bank-loan"]["filters"] if not f["passed"]] == ["amount", "business_age", "revenue", "security", "documents", "funding_time"]
    assert "Needs at least 24 months trading; 18 declared." in sec and "Requires a collateral, which was not declared as available." in sec
    assert "Missing documents: tax_certificate." in sec and "Typically takes 21 day(s) to fund; funds are needed in 7." in sec
    assert [f["code"] for f in ex["ke-trade-line"]["filters"] if not f["passed"]] == ["jurisdiction", "currency", "amount", "business_age", "revenue", "funding_time"][:2] + \
        [f["code"] for f in ex["ke-trade-line"]["filters"] if not f["passed"]][2:]
    assert ex["ke-trade-line"]["reasons"][0] == "Not offered in BD (offered in KE)."
    assert ex["wallet-microloan"]["source"] == {"label": "Illustrative sample terms", "updated": "2026-10-01", "catalogue_version": "catalogue-2026.10"}
    assert "forecast" not in ex["wallet-microloan"] and "terms" not in ex["wallet-microloan"]      # an excluded product is never costed or ranked


@pytest.mark.parametrize("change, code, fragment", [
    ({"purpose": "equipment"}, "purpose", "supports working_capital"), ({"months_trading": 5}, "business_age", "Needs at least 6 months"),
    ({"documents_available": ["id"]}, "documents", "Missing documents: bank_statements"), ({"needed_by": "2026-10-22"}, "funding_time", "funds are needed in 2"),
    ({"security_available": []}, "security", "Requires a guarantor"),
])
def test_each_hard_filter_excludes_with_its_reason(change, code, fragment):
    res = match(_inputs(), CATALOGUE, _req(**change))
    product = {"purpose": "wc-line", "months_trading": "wc-line", "documents_available": "wc-line", "needed_by": "wc-line", "security_available": "term-loan"}[next(iter(change))]
    ex = next(p for p in res["excluded"] if p["id"] == product)
    failed = next(f for f in ex["filters"] if f["code"] == code)
    assert not failed["passed"] and fragment in failed["message"]
    assert product not in [p["id"] for p in res["eligible"]]


def test_revenue_and_receivable_security_filters_use_the_records():
    low = match(_inputs(revenue_90d=3_000_000), CATALOGUE, _req())          # 1,000,000 a month: only the micro-loan's minimum is met
    assert [p["id"] for p in low["eligible"]] == []
    assert "records show 1000000 over the last 90 days" in next(p for p in low["excluded"] if p["id"] == "wc-line")["reasons"][0]
    few = match(_inputs(receivables=4_000_000), CATALOGUE, _req())          # 80% of 4,000,000 = 3,200,000 < 4,000,000
    adv = next(p for p in few["excluded"] if p["id"] == "invoice-advance")
    assert "(3200000 minor units available); 4000000 requested" in adv["reasons"][0]


def test_calendar_month_end_and_invalid_requests():
    assert str(add_months(__import__("datetime").date(2026, 1, 31), 1)) == "2026-02-28" and str(add_months(__import__("datetime").date(2026, 12, 15), 2)) == "2027-02-15"
    for change, msg in [({"receiving_account_id": "nope"}, "not an account"), ({"needed_by": "2026-10-20"}, "must be after"), ({"needed_by": "2027-06-01"}, "13-week")]:
        with pytest.raises(MatchError, match=msg):
            match(_inputs(), CATALOGUE, _req(**change))
    with pytest.raises(MatchError, match="no verified balance"):
        match(_inputs(balance=None), CATALOGUE, _req())


def test_deterministic_and_inputs_untouched():
    inputs, req = _inputs(), _req()
    before = copy.deepcopy(inputs)
    assert match(inputs, CATALOGUE, req) == match(inputs, CATALOGUE, req) and inputs == before


def test_catalogue_is_versioned_illustrative_and_dated():
    assert CATALOGUE["version"] == "catalogue-2026.10" and "Not taken from any real lender" in CATALOGUE["source"]
    assert all(p["source"]["updated"] and "Illustrative" in p["provider"] for p in CATALOGUE["products"])


# --- API -----------------------------------------------------------------------------------------------


def _setup():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    bank = biz.account["ACC-BANK"]
    day = lambda n: (biz.today - timedelta(days=n)).isoformat()
    biz.post_rows("ACC-BANK", [(day(60), "Sales A", 10_000_000, "S1"), (day(40), "Sales B", 10_000_000, "S2"), (day(20), "Sales C", 10_000_000, "S3")])
    items = [{"txn_id": str(r.id), "kind": "operating_receipt", "category": "sales"} for r in _txns(biz)]
    assert biz.owner.post(f"{biz.base}/classifications", json={"items": items}).status_code == 200
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": biz.today.isoformat(), "balance_minor": 10_000_000, "currency": "BDT",
                                                            "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    assert biz.owner.post(f"{biz.base}/obligations", json={
        "direction": "payable", "party_name": "Landlord", "document_number": "RENT", "issue_date": day(5), "due_date": (biz.today + timedelta(days=7)).isoformat(),
        "amount_minor": 5_000_000, "currency": "BDT", "category": "rent", "source_reference": "book", "settlement_account_id": bank}).status_code == 201
    assert biz.owner.post(f"{biz.base}/obligations", json={            # 20,000,000 of outstanding, undisputed invoices backs the invoice advance
        "direction": "receivable", "party_name": "Big Customer", "document_number": "INV-BIG", "issue_date": day(5), "due_date": (biz.today + timedelta(days=30)).isoformat(),
        "amount_minor": 20_000_000, "currency": "BDT", "category": "sales", "source_reference": "book", "settlement_account_id": bank}).status_code == 201
    return biz


def _txns(biz):
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b"), {"b": biz.id}).all()


def _body(biz, **kw):
    return {"amount_minor": 4_000_000, "currency": "BDT", "needed_by": (biz.today + timedelta(days=7)).isoformat(), "purpose": "working_capital",
            "receiving_account_id": biz.account["ACC-BANK"], "months_trading": 18, "security_available": ["guarantor"], "documents_available": ALL_DOCS} | kw


def test_saved_run_through_the_api_with_records_driven_filters():
    biz = _setup()
    r = biz.owner.post(f"{biz.base}/funding-matches", json=_body(biz))
    assert r.status_code == 201, r.text
    run = r.json()
    res = run["results"]
    assert sorted(p["id"] for p in res["eligible"]) == ["invoice-advance", "term-loan", "wc-line"]   # revenue from classified records: 30,000,000 / 90 days
    assert [p["rank"] for p in res["eligible"]] == [1, 2, 3]
    assert res["baseline"]["cautious"]["first_breach"]["gap_minor"] == 3_000_000 and run["catalogue_version"] == "catalogue-2026.10"
    assert biz.owner.get(f"{biz.base}/funding-matches/{run['id']}").json()["results"] == res
    assert [x["id"] for x in biz.owner.get(f"{biz.base}/funding-matches").json()] == [run["id"]]
    v = biz.owner.get(f"{biz.base}/funding-matches/{run['id']}/verify").json()
    assert v["inputs_hash_matches"] and v["results_reproduce"]
    cat = biz.owner.get(f"{biz.base}/funding-matches/catalogue").json()
    assert cat["version"] == "catalogue-2026.10" and len(cat["products"]) == 6


def test_validation_permissions_isolation_audit_and_no_sharing_routes():
    biz, other = _setup(), _setup()
    bad = lambda **kw: biz.owner.post(f"{biz.base}/funding-matches", json=_body(biz, **kw))
    assert bad(amount_minor=1.5).status_code == 422 and bad(purpose="gambling").status_code == 422 and bad(currency="XXX").status_code == 422
    assert bad(security_available=["gold"]).status_code == 422 and bad(receiving_account_id=other.account["ACC-BANK"]).status_code == 422
    assert bad(receiving_account_id=biz.account["ACC-CASH"]).status_code == 422                       # no verified balance: cannot model the funds
    assert biz.owner.post(f"{biz.base}/funding-matches", json=_body(biz) | {"lender": "x"}).status_code == 422
    staff = biz.member("staff")
    assert staff.post(f"{biz.base}/funding-matches", json=_body(biz)).status_code == 403
    assert staff.get(f"{biz.base}/funding-matches").status_code == 403 and staff.get(f"{biz.base}/funding-matches/catalogue").status_code == 403
    rid = biz.member("bookkeeper").post(f"{biz.base}/funding-matches", json=_body(biz)).json()["id"]
    for url in (f"{biz.base}/funding-matches", f"{biz.base}/funding-matches/{rid}", f"{biz.base}/funding-matches/{rid}/verify", f"{biz.base}/funding-matches/catalogue"):
        assert other.owner.get(url).status_code == 404
    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    assert any(e["action"] == "funding.run" and e["outcome"] == "success" and e["details"]["eligible"] == 3 for e in events)
    assert any(e["action"] == "funding.run" and e["outcome"] == "denied" for e in events)
    from app.main import app
    routes = {(m, r.path) for r in app.routes if "funding-matches" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert {m for m, _ in routes} == {"GET", "POST"} and not any(p.rsplit("/", 1)[-1] in {"apply", "submit", "share", "approve"} for _, p in routes)
    with get_engine().connect() as conn:
        try:
            conn.execute(text("UPDATE funding_match_run SET catalogue_version = 'x' WHERE business_id = :b"), {"b": biz.id})
            raise AssertionError("funding_match_run must be append-only")
        except Exception as exc:  # noqa: BLE001
            assert "append-only" in str(exc)

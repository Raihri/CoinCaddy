"""Smart Alerts: hand-checked rule results (pure engine), then the API: dedup on retry, resolution, access, privacy."""

import copy
import re
import uuid
from datetime import timedelta

from sqlalchemy import text

from app.db import get_engine
from app.modules.alerts.engine import evaluate, median
from app.modules.records.service import business_today
from tests.helpers import Biz, make_user

AS_OF = "2026-10-20"


def _ob(oid, direction, party, num, due, outstanding, *, category, days_overdue=0, disputed=False):
    return {"id": oid, "direction": direction, "party": {"id": "p-" + oid, "name": party}, "document_number": num, "due_date": due,
            "outstanding_minor": outstanding, "currency": "BDT", "category": category, "disputed": disputed, "status": "open",
            "days_overdue": days_overdue, "settlement_account_id": None}


def _inputs(balance, obligations, outflows=()):
    outlook_obs = [{"id": o["id"], "direction": o["direction"], "party": o["party"]["name"], "document_number": o["document_number"],
                    "due_date": o["due_date"], "outstanding_minor": o["outstanding_minor"], "currency": "BDT", "disputed": o["disputed"],
                    "settlement_account_id": "acc-bank"} for o in obligations]
    return {"as_of": AS_OF, "outflows": list(outflows), "obligations": obligations, "outlook": {
        "calc_version": "outlook-v1", "as_of": AS_OF, "base_currency": "BDT", "reserve": {"amount_minor": 8_000_000, "currency": "BDT"},
        "accounts": [{"id": "acc-bank", "label": "Bank", "currency": "BDT", "usable_minor": balance, "freshness": "fresh",
                      "evidence_through": AS_OF, "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "an-1", "restriction_releases": []}],
        "obligations": outlook_obs, "unmatched_transfers": []}}


def _out(i, day, amount, desc="Supplier"):
    return {"id": f"tx-{i}", "account_id": "acc-bank", "business_date": day, "amount_minor": amount, "currency": "BDT", "description": desc}


# Hand-worked case. Bank 10,000,000, reserve 8,000,000, as of 20 Oct 2026.
#   LOAN-1  loan repayment 3,000,000 due 25 Oct  -> that day closes at 10,000,000 - 3,000,000 = 7,000,000 < 8,000,000
#   INV-OLD 500,000 due 5 Oct (15 days overdue)  -> overdue alert (medium); expected path re-collects it 27 Oct (7,500,000 < reserve)
#   INV-DISP disputed and overdue                -> no alert; INV-OK due 30 Oct, not overdue -> no alert
#   Outflows: baseline 90,000 100,000 105,000 110,000 120,000 -> median 105,000, trigger level 3 x = 315,000
#             recent 300,000 (below) and 400,000 (above: 400,000 x 10 // 105,000 = 38 -> "3.8 times")
CASE_OBS = [
    _ob("LOAN-1", "payable", "Eastern Bank", "LOAN-1", "2026-10-25", 3_000_000, category="loan_repayment"),
    _ob("INV-OLD", "receivable", "Delta Foods", "INV-OLD", "2026-10-05", 500_000, category="sales", days_overdue=15),
    _ob("INV-DISP", "receivable", "Rafi Stores", "INV-DISP", "2026-09-01", 900_000, category="sales", days_overdue=49, disputed=True),
    _ob("INV-OK", "receivable", "Nova Ltd", "INV-OK", "2026-10-30", 700_000, category="sales"),
]
CASE_OUT = [_out(1, "2026-08-02", 90_000), _out(2, "2026-08-20", 100_000), _out(3, "2026-09-05", 105_000), _out(4, "2026-09-20", 110_000),
            _out(5, "2026-10-10", 120_000), _out(6, "2026-10-18", 400_000, "Bulk order"), _out(7, "2026-10-19", 300_000, "Small order")]


def by_key(alerts):
    return {a["key"]: a for a in alerts}


def test_median_is_exact_for_odd_and_even_counts():
    assert median([5, 1, 3]) == 3 and median([1, 2, 3, 4]) == 2  # integer floor of 2.5


def test_true_triggers_have_exact_date_amount_severity_and_record_ids():
    a = by_key(evaluate(_inputs(10_000_000, CASE_OBS, CASE_OUT)))
    assert set(a) == {"reserve_breach:BDT", "repayment_pressure:LOAN-1", "overdue_invoice:INV-OLD", "unusual_outflow:tx-6"}

    rb = a["reserve_breach:BDT"]
    assert (rb["severity"], rb["alert_date"], rb["amount_minor"]) == ("high", "2026-10-25", 1_000_000)
    assert rb["details"]["expected"]["balance_minor"] == 7_000_000 and rb["details"]["cautious"]["date"] == "2026-10-25"
    assert {"kind": "obligation", "id": "LOAN-1"} in rb["record_ids"] and {"kind": "account", "id": "acc-bank"} in rb["record_ids"]

    rp = a["repayment_pressure:LOAN-1"]
    assert (rp["severity"], rp["alert_date"], rp["amount_minor"]) == ("high", "2026-10-25", 3_000_000)
    assert rp["record_ids"] == [{"kind": "obligation", "id": "LOAN-1"}] and rp["details"]["balance_expected_minor"] == 7_000_000

    od = a["overdue_invoice:INV-OLD"]
    assert (od["severity"], od["alert_date"], od["amount_minor"]) == ("medium", "2026-10-05", 500_000)
    assert "15 day(s) overdue" in od["cause"] and od["record_ids"] == [{"kind": "obligation", "id": "INV-OLD"}]

    uo = a["unusual_outflow:tx-6"]
    assert (uo["severity"], uo["alert_date"], uo["amount_minor"]) == ("medium", "2026-10-18", 400_000)
    assert uo["details"]["median_minor"] == 105_000 and "3.8 times" in uo["cause"]
    assert uo["record_ids"][0] == {"kind": "ledger_transaction", "id": "tx-6"}


def test_non_triggers():
    a = by_key(evaluate(_inputs(10_000_000, CASE_OBS, CASE_OUT)))
    assert "overdue_invoice:INV-DISP" not in a and "overdue_invoice:INV-OK" not in a  # disputed / not yet due
    assert "unusual_outflow:tx-7" not in a                                          # 300,000 < 3 x 105,000
    rich = by_key(evaluate(_inputs(50_000_000, [CASE_OBS[0]])))                      # plenty of cash: nothing to warn about
    assert rich == {}


def test_unusual_outflow_needs_a_baseline_and_a_recent_date():
    few = [_out(i, f"2026-09-0{i}", 100_000) for i in range(1, 5)] + [_out(9, "2026-10-19", 900_000)]
    assert evaluate(_inputs(50_000_000, [], few)) == []                              # only 4 baseline outflows
    old = CASE_OUT[:5] + [_out(8, "2026-10-13", 900_000)]                            # 13 Oct is outside the last 7 days...
    assert not any(x["type"] == "unusual_outflow" and x["key"] == "unusual_outflow:tx-8" for x in evaluate(_inputs(50_000_000, [], old)))
    edge = CASE_OUT[:5] + [_out(8, "2026-10-14", 315_000)]                           # 14 Oct is inside; exactly 3 x median triggers
    assert "unusual_outflow:tx-8" in by_key(evaluate(_inputs(50_000_000, [], edge)))


def test_severity_bands_and_cautious_only_breach():
    obs = [_ob("A", "receivable", "A", "A", "2026-10-19", 1, category="sales", days_overdue=1),
           _ob("B", "receivable", "B", "B", "2026-10-13", 1, category="sales", days_overdue=7),
           _ob("C", "receivable", "C", "C", "2026-10-12", 1, category="sales", days_overdue=8),
           _ob("D", "receivable", "D", "D", "2026-09-20", 1, category="sales", days_overdue=30),
           _ob("E", "receivable", "E", "E", "2026-09-19", 1, category="sales", days_overdue=31)]
    sev = {k.split(":")[1]: v["severity"] for k, v in by_key(evaluate(_inputs(50_000_000, obs))).items()}
    assert sev == {"A": "low", "B": "low", "C": "medium", "D": "medium", "E": "high"}
    # Cash 10,000,000, bill 2,500,000 due 24 Oct, invoice 1,000,000 due 24 Oct arrives 14 days late on the cautious path:
    # expected closes 8,500,000 (above reserve), cautious 7,500,000 (below) -> medium, cautious-only.
    mixed = [_ob("BILL", "payable", "Rent Co", "BILL", "2026-10-24", 2_500_000, category="rent"),
             _ob("INV", "receivable", "Zed", "INV", "2026-10-24", 1_000_000, category="sales")]
    rb = by_key(evaluate(_inputs(10_000_000, mixed)))["reserve_breach:BDT"]
    assert (rb["severity"], rb["alert_date"], rb["amount_minor"], rb["details"]["expected"]) == ("medium", "2026-10-24", 500_000, None)


def test_evaluation_is_deterministic_and_does_not_mutate_inputs():
    inputs = _inputs(10_000_000, CASE_OBS, CASE_OUT)
    before = copy.deepcopy(inputs)
    assert evaluate(inputs) == evaluate(inputs) and inputs == before


# --- API -----------------------------------------------------------------------------------------------


def _bill(biz, num, direction, days, amount, category, party="Party"):
    r = biz.owner.post(f"{biz.base}/obligations", json={
        "direction": direction, "party_name": party, "document_number": num, "issue_date": (biz.today - timedelta(days=60)).isoformat(),
        "due_date": (biz.today + timedelta(days=days)).isoformat(), "amount_minor": amount, "currency": "BDT", "category": category,
        "source_reference": "book", "settlement_account_id": biz.account["ACC-BANK"]})
    assert r.status_code == 201, r.text
    return r.json()


def _setup(balance=9_000_000):
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    r = biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": biz.account["ACC-BANK"], "anchor_date": biz.today.isoformat(),
                                                         "balance_minor": balance, "currency": "BDT", "source_kind": "bank_statement",
                                                         "source_reference": "Today"})
    assert r.status_code == 201, r.text
    return biz


def _run(biz, client=None):
    r = (client or biz.owner).post(f"{biz.base}/alerts/run")
    assert r.status_code == 200, r.text
    return r.json()


def test_true_trigger_non_trigger_and_no_duplicate_on_retry():
    biz = _setup()
    assert _run(biz)["alerts"] == []                                        # nothing wrong yet: non-trigger
    loan = _bill(biz, "LOAN-A", "payable", 3, 3_000_000, "loan_repayment", "Eastern Bank")
    inv = _bill(biz, "INV-LATE", "receivable", -12, 2_500_000, "sales", "Late Customer")
    first = _run(biz)
    assert (first["created"], first["resolved"]) == (3, 0)
    kinds = {a["type"]: a for a in first["alerts"]}
    assert set(kinds) == {"reserve_breach", "repayment_pressure", "overdue_invoice"}
    # 9,000,000 - 3,000,000 on day 3 = 6,000,000; the overdue invoice (+2,500,000 on day 7 in the expected path) does not help by then.
    rb = kinds["reserve_breach"]
    assert rb["date"] == (biz.today + timedelta(days=3)).isoformat() and rb["amount_minor"] == 2_000_000 and rb["severity"] == "high"
    assert kinds["overdue_invoice"]["amount_minor"] == 2_500_000 and kinds["overdue_invoice"]["record_ids"] == [{"kind": "obligation", "id": inv["id"]}]
    assert {"kind": "obligation", "id": loan["id"]} in rb["record_ids"]

    ids = {a["id"] for a in first["alerts"]}
    for _ in range(2):                                                      # retries: same alerts, none new
        again = _run(biz)
        assert (again["created"], again["updated"], again["resolved"], again["unchanged"]) == (0, 0, 0, 3)
        assert {a["id"] for a in again["alerts"]} == ids
        assert again["notification_text"] is None
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alert WHERE business_id = :b"), {"b": biz.id}).scalar_one() == 3
        assert conn.execute(text("SELECT max(detection_count) FROM alert WHERE business_id = :b"), {"b": biz.id}).scalar_one() == 3


def test_alert_resolves_when_the_cause_is_gone_and_returns_as_a_new_alert():
    biz = _setup()
    inv = _bill(biz, "INV-LATE", "receivable", -12, 2_500_000, "sales")
    assert _run(biz)["created"] == 1
    pay = biz.owner.post(f"{biz.base}/obligations/{inv['id']}/allocations", json={
        "amount_minor": 2_500_000, "currency": "BDT", "allocation_date": biz.today.isoformat()})
    assert pay.status_code == 201, pay.text
    r = _run(biz)
    assert (r["created"], r["resolved"], r["alerts"]) == (0, 1, [])
    resolved = biz.owner.get(f"{biz.base}/alerts?status=resolved").json()["alerts"]
    assert len(resolved) == 1 and resolved[0]["status"] == "resolved" and resolved[0]["resolved_at"]


def test_partial_payment_updates_the_same_alert_instead_of_duplicating():
    biz = _setup()
    inv = _bill(biz, "INV-LATE", "receivable", -40, 2_500_000, "sales")
    a0 = _run(biz)["alerts"][0]
    assert a0["severity"] == "high" and a0["amount_minor"] == 2_500_000
    biz.owner.post(f"{biz.base}/obligations/{inv['id']}/allocations", json={"amount_minor": 1_000_000, "currency": "BDT",
                                                                           "allocation_date": biz.today.isoformat()})
    r = _run(biz)
    assert (r["created"], r["updated"], r["resolved"]) == (0, 1, 0)
    assert r["alerts"][0]["id"] == a0["id"] and r["alerts"][0]["amount_minor"] == 1_500_000


def test_unusual_outflow_through_the_real_import_path():
    biz = Biz()
    base = biz.today = business_today("Asia/Dhaka")
    day = lambda n: (base - timedelta(days=n)).isoformat()
    rows = [(day(n), f"Supplier {n}", -amt, f"S{n}") for n, amt in ((50, 100_000), (40, 120_000), (30, 105_000), (20, 110_000), (12, 90_000))]
    rows += [(day(1), "Bulk order", -400_000, "BULK"), (day(2), "Small order", -300_000, "SMALL")]
    biz.post_rows("ACC-BANK", rows)
    r = _run(biz)
    assert [a["type"] for a in r["alerts"]] == ["unusual_outflow"]
    a = r["alerts"][0]
    assert (a["amount_minor"], a["date"], a["details"]["median_minor"]) == (400_000, day(1), 105_000)
    assert _run(biz)["created"] == 0
    # An outflow already allocated to a bill is a planned payment, not an unusual one.
    with get_engine().connect() as conn:
        txn = conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b AND reference = 'BULK'"), {"b": biz.id}).scalar_one()
    bill = _bill(biz, "BILL-BULK", "payable", -1, 400_000, "inventory_suppliers", "Bulk Supplier")
    biz.owner.post(f"{biz.base}/obligations/{bill['id']}/allocations", json={
        "amount_minor": 400_000, "currency": "BDT", "allocation_date": day(1), "ledger_txn_id": str(txn)})
    r = _run(biz)
    assert r["resolved"] == 1 and r["alerts"] == []


def test_notification_text_is_minimal_and_detail_needs_login():
    biz = _setup()
    _bill(biz, "INV-SECRET-77", "receivable", -12, 2_500_000, "sales", "Secret Customer Ltd")
    r = _run(biz)
    note = r["notification_text"]
    assert note == "SME Finance Copilot: 1 new alert(s) need your attention. Sign in to view details."
    assert not re.search(r"Secret|INV-|BDT|2,?500|overdue", note)            # no party, amount, document, or cause
    anon = make_user()[1]
    anon.cookies.clear()
    assert anon.get(f"{biz.base}/alerts").status_code == 401
    assert anon.get(f"{biz.base}/alerts/{r['alerts'][0]['id']}").status_code == 401
    with get_engine().connect() as conn:
        stored = conn.execute(text("SELECT notification_text FROM alert_run WHERE business_id = :b"), {"b": biz.id}).scalar_one()
    assert stored == note


def test_permissions_isolation_audit_and_immutability():
    biz = _setup()
    _bill(biz, "INV-LATE", "receivable", -12, 2_500_000, "sales")
    staff, bookkeeper = biz.member("staff"), biz.member("bookkeeper")
    assert staff.post(f"{biz.base}/alerts/run").status_code == 403           # staff cannot run...
    assert _run(biz, bookkeeper)["created"] == 1                              # ...a bookkeeper can
    listing = staff.get(f"{biz.base}/alerts")                                 # ...and staff can read
    assert listing.status_code == 200 and len(listing.json()["alerts"]) == 1
    alert_id = listing.json()["alerts"][0]["id"]

    other = _setup()
    assert other.owner.get(f"{biz.base}/alerts").status_code == 404           # another business: same answer as unknown
    assert other.owner.get(f"{biz.base}/alerts/{alert_id}").status_code == 404
    assert other.owner.post(f"{biz.base}/alerts/run").status_code == 404
    assert other.owner.get(f"{other.base}/alerts/{alert_id}").status_code == 404   # an alert ID from elsewhere is not found here
    assert other.owner.get(f"{other.base}/alerts").json()["alerts"] == []
    assert biz.owner.get(f"{biz.base}/alerts/{uuid.uuid4()}").status_code == 404

    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    runs = [e for e in events if e["action"] == "alerts.run"]
    assert sorted(e["outcome"] for e in runs) == ["denied", "success"]       # the staff refusal is recorded too
    assert next(e for e in runs if e["outcome"] == "success")["details"]["created"] == 1
    with get_engine().connect() as conn:
        conn.execute(text("SAVEPOINT s"))
        try:
            conn.execute(text("UPDATE alert_run SET created_count = 99 WHERE business_id = :b"), {"b": biz.id})
            raise AssertionError("alert_run must be append-only")
        except Exception as exc:  # noqa: BLE001
            assert "append-only" in str(exc)


def test_cross_business_data_never_enters_the_run():
    biz, other = _setup(), _setup()
    _bill(other, "INV-OTHER", "receivable", -12, 2_500_000, "sales", "Other Business Customer")
    assert _run(biz)["alerts"] == []
    assert _run(other)["created"] == 1 and biz.owner.get(f"{biz.base}/alerts").json()["alerts"] == []


def test_concurrent_runs_still_create_each_alert_once():
    from concurrent.futures import ThreadPoolExecutor
    biz = _setup()
    _bill(biz, "INV-LATE", "receivable", -12, 2_500_000, "sales")
    _bill(biz, "LOAN-A", "payable", 3, 3_000_000, "loan_repayment")
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(lambda _: biz.owner.post(f"{biz.base}/alerts/run"), range(6)))
    assert all(r.status_code == 200 for r in results)
    assert sum(r.json()["created"] for r in results) == 3
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alert WHERE business_id = :b AND status = 'open'"), {"b": biz.id}).scalar_one() == 3

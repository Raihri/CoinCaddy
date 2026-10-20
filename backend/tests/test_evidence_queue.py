"""Decision-Impact Evidence Queue: exact ranking and effects (pure engine), then the API: the decision changes only after record review."""

import copy
from datetime import timedelta

from sqlalchemy import text

from app.db import get_engine
from app.modules.evidence_queue.engine import build_queue
from app.modules.payment_review.engine import review
from app.modules.records.service import business_today
from tests.helpers import Biz
from tests.test_payment_review import ADVERSE, CASE_OBS, _inputs, _ob, _req


def queue(inputs, req, now=None):
    saved = review(inputs, req)
    return saved, build_queue(inputs, req, saved, now)


def ids(q):
    return [t["id"] for t in q["tasks"]]


MILD = {"receipt_delay_days": 1, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}


def test_material_missing_balance_ranks_above_an_immaterial_receipt():
    # The funding account has no verified balance, and a 50,000 invoice is overdue (reserve 8,000,000, so 1% = 80,000).
    obs = [_ob("R-small", "receivable", "Tiny Customer", "INV-9", "2026-10-05", 50_000)]
    saved, q = queue(_inputs(balance=None, freshness="no_anchor", obligations=obs), _req(linked_payable_id=None, adverse=MILD, payment_date="2026-10-30"))
    assert saved["decision"] == "more_information_needed" and q["decision_on_record"] == "more_information_needed"
    assert ids(q) == ["balance:bank", "balance:box", "overdue:R-small"] and q["materiality_threshold_minor"] == 80_000
    first, last = q["tasks"][0], q["tasks"][-1]
    assert (first["rank"], first["tier"], first["blocks_positive_result"], first["effect"]["bounded"]) == (1, "blocking", True, False)
    assert first["source"] == {"kind": "account", "id": "bank", "label": "Bank"} and "no verified balance" in first["why"]
    assert (last["tier"], last["effect"]["swing_minor"], last["blocks_positive_result"]) == ("immaterial", 0, False)


def test_exact_bounded_effects_and_decision_changing_rank():
    # CASE: shortfall only because the delayed path hits 7,000,000 on 28 Oct, when the 1,000,000 bill TRN-1 is paid.
    # If the TRN-1 dispute were upheld (bill removed) every path would hold at exactly 8,000,000.
    obs = [dict(o, disputed=True) if o["id"] == "P3" else o for o in CASE_OBS]
    saved, q = queue(_inputs(obligations=obs), _req(adverse=MILD))
    assert saved["decision"] == "reserve_shortfall"
    # Order: the decision-changing bill dispute; the overdue invoice R2 (material: if it never arrives, the largest-missed path also
    # loses it and drops to 7,000,000 on 28 Oct); the unbounded missing balance; the disputed invoice R3 (arrives after every low point).
    assert ids(q) == ["dispute:P3", "overdue:R2", "balance:box", "dispute:R3"]
    r2 = q["tasks"][1]
    assert r2["tier"] == "material" and r2["effect"]["swing_minor"] == 1_000_000 and r2["effect"]["worst"]["minimum_by_path"]["largest_missed"] == 7_000_000
    assert not r2["effect"]["flips_decision"]                                    # the decision is a shortfall either way
    t = q["tasks"][0]
    assert (t["tier"], t["kind"], t["amount_minor"]) == ("decision_changing", "disputed_payable", 1_000_000)
    e = t["effect"]
    assert e["bounded"] and e["flips_decision"] and (e["best_decision"], e["worst_decision"]) == ("within_reserve", "reserve_shortfall")
    assert e["current"]["minimum_by_path"]["receipts_delayed"] == 7_000_000
    assert e["best"]["minimum_by_path"] == {"on_time": 8_000_000, "receipts_delayed": 8_000_000, "largest_missed": 8_000_000, "adverse": 8_000_000}
    assert e["worst"] == e["current"] and e["swing_minor"] == 1_000_000
    tiers = [x["tier"] for x in q["tasks"]]
    assert tiers == ["decision_changing", "material", "unbounded_upside", "immaterial"]
    assert q["tasks"][2]["effect"]["bounded"] is False and "could only add cash" in q["tasks"][2]["effect"]["note"]
    assert q["tasks"][3]["effect"]["swing_minor"] == 0
    assert q["decision_on_record"] == "reserve_shortfall"                        # the displayed decision did not move


def test_materiality_threshold_orders_by_swing():
    # Payment 6,000,000 on 30 Oct, unlinked, no other bills. Three overdue invoices expected on 27 Oct (before the payment).
    obs = [_ob("R2", "receivable", "Northwind", "INV-2", "2026-10-05", 3_000_000), _ob("Rm", "receivable", "Mid Customer", "INV-M", "2026-10-05", 100_000),
           _ob("Rs", "receivable", "Small Customer", "INV-S", "2026-10-05", 50_000)]
    saved, q = queue(_inputs(obligations=obs), _req(linked_payable_id=None, adverse=MILD, payment_date="2026-10-30"))
    assert saved["decision"] == "within_reserve"
    by = {t["id"]: t for t in q["tasks"]}
    # Dropping an invoice lowers the on-time lowest cash by exactly its amount.
    assert [(k, by[k]["effect"]["swing_minor"], by[k]["tier"]) for k in ("overdue:R2", "overdue:Rm", "overdue:Rs")] == [
        ("overdue:R2", 3_000_000, "material"), ("overdue:Rm", 100_000, "material"), ("overdue:Rs", 50_000, "immaterial")]
    assert ids(q) == ["overdue:R2", "overdue:Rm", "balance:box", "overdue:Rs"]          # unbounded upside sits above immaterial
    assert by["overdue:Rs"]["effect"]["worst"]["minimum_by_path"]["on_time"] == 13_100_000 and by["overdue:Rs"]["effect"]["current"]["minimum_by_path"]["on_time"] == 13_150_000
    assert by["overdue:R2"]["effect"]["flips_decision"] is False


def test_blocker_order_funding_then_linked_dispute_then_other_balances_then_transfers():
    obs = [dict(o, disputed=True) if o["id"] == "P2" else o for o in CASE_OBS]
    inputs = _inputs(freshness="stale", obligations=obs, transfers=[{"id": "tr-1"}])
    inputs["accounts"].append({"id": "wallet", "label": "Wallet", "currency": "BDT", "usable_minor": 1_000_000, "freshness": "stale", "evidence_through": "2026-09-01",
                               "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "a2", "restriction_releases": []})
    saved, q = queue(inputs, _req())
    blocking = [t["id"] for t in q["tasks"] if t["tier"] == "blocking"]
    assert blocking == ["balance:bank", "dispute:P2", "balance:wallet", "transfer:tr-1"]
    assert all(t["blocks_positive_result"] for t in q["tasks"] if t["tier"] == "blocking")
    assert [t["rank"] for t in q["tasks"]] == list(range(1, len(q["tasks"]) + 1))
    assert q["tasks"][blocking.index("transfer:tr-1")]["effect"]["bounded"] is False        # no amount in the inputs: no guess


def test_queue_never_changes_the_review_or_creates_a_positive_result():
    inputs, req = _inputs(freshness="stale", obligations=[o for o in CASE_OBS if o["id"] != "P3"]), _req(adverse=MILD)
    before, saved = copy.deepcopy(inputs), review(inputs, req)
    q = build_queue(inputs, req, saved)
    assert inputs == before and q["decision_on_record"] == saved["decision"] == "more_information_needed"
    assert q["reproduces_saved_review"] is True and q == build_queue(inputs, req, saved)    # deterministic
    # Even the most optimistic single-record outcome never reports "within_reserve" while a blocker remains.
    for t in q["tasks"]:
        if t["effect"]["best"]:
            assert t["effect"]["best"]["decision"] != "within_reserve"


def test_changed_records_are_flagged_not_resolved():
    inputs, req = _inputs(freshness="stale", obligations=[o for o in CASE_OBS if o["id"] != "P3"]), _req(adverse=MILD)
    saved = review(inputs, req)
    now = copy.deepcopy(inputs)
    now["accounts"][0].update(freshness="fresh", evidence_through=now["as_of"])
    q = build_queue(inputs, req, saved, now)
    t = next(t for t in q["tasks"] if t["id"] == "balance:bank")
    assert q["records_changed_since_review"] is True and t["status"] == "record_changed" and "Run a new Payment Review" in t["status_note"]
    assert q["decision_on_record"] == "more_information_needed"                  # still the saved decision
    assert build_queue(inputs, req, saved, copy.deepcopy(inputs))["records_changed_since_review"] is False


# --- API -----------------------------------------------------------------------------------------------


def _setup():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    bank, cash = biz.account["ACC-BANK"], biz.account["ACC-CASH"]
    biz.anchor(bank, 20_000_000)
    r = biz.owner.post(f"{biz.base}/obligations", json={
        "direction": "receivable", "party_name": "Tiny Customer", "document_number": "INV-S", "issue_date": (biz.today - timedelta(days=40)).isoformat(),
        "due_date": (biz.today - timedelta(days=10)).isoformat(), "amount_minor": 50_000, "currency": "BDT", "category": "sales",
        "source_reference": "book", "settlement_account_id": bank})
    assert r.status_code == 201, r.text
    biz.small = r.json()["id"]
    return biz


def _anchor(self, account_id, amount):
    r = self.owner.post(f"{self.base}/cash/anchors", json={"account_id": account_id, "anchor_date": self.today.isoformat(), "balance_minor": amount,
                                                           "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Today"})
    assert r.status_code == 201, r.text


Biz.anchor = _anchor


def _review(biz):
    r = biz.owner.post(f"{biz.base}/payment-reviews", json={
        "account_id": biz.account["ACC-CASH"], "amount_minor": 1_000_000, "payment_date": (biz.today + timedelta(days=3)).isoformat(), "payee": "Supplier",
        "adverse": {"receipt_delay_days": 3, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}})
    assert r.status_code == 201, r.text
    return r.json()


def test_decision_changes_only_after_authorized_record_review():
    biz = _setup()
    rep = _review(biz)
    assert rep["decision"] == "more_information_needed" and rep["results"]["arithmetic_state"] == "not_assessed"
    url = f"{biz.base}/payment-reviews/{rep['id']}/evidence-queue"
    with get_engine().connect() as conn:
        counts = lambda: (conn.execute(text("SELECT count(*) FROM payment_review WHERE business_id = :b"), {"b": biz.id}).scalar_one(),
                          conn.execute(text("SELECT count(*) FROM audit_event WHERE business_id = :b"), {"b": biz.id}).scalar_one())
        before = counts()
        q = biz.owner.get(url).json()
        assert q == biz.owner.get(url).json() and counts() == before                  # read-only and repeatable
    first = q["tasks"][0]
    assert (first["id"], first["tier"], first["rank"], first["status"]) == (f"balance:{biz.account['ACC-CASH']}", "blocking", 1, "open")
    assert first["source"]["id"] == biz.account["ACC-CASH"]
    assert q["tasks"][-1]["id"] == f"overdue:{biz.small}" and q["tasks"][-1]["tier"] == "immaterial"
    assert q["decision_on_record"] == "more_information_needed"

    # Staff cannot enter balance records, so they cannot change the evidence.
    staff = biz.member("staff")
    assert staff.get(url).status_code == 403
    assert staff.post(f"{biz.base}/cash/anchors", json={"account_id": biz.account["ACC-CASH"], "anchor_date": biz.today.isoformat(), "balance_minor": 2_000_000,
                                                        "currency": "BDT", "source_kind": "bank_statement", "source_reference": "x"}).status_code == 403
    assert biz.owner.get(url).json()["tasks"][0]["status"] == "open"

    biz.anchor(biz.account["ACC-CASH"], 2_000_000)                                    # the authorized owner reviews and records the balance
    after = biz.owner.get(url).json()
    assert after["decision_on_record"] == "more_information_needed"                  # the saved review did not move
    assert after["records_changed_since_review"] is True and after["tasks"][0]["status"] == "record_changed"
    assert biz.owner.get(f"{biz.base}/payment-reviews/{rep['id']}").json()["decision"] == "more_information_needed"
    new = _review(biz)                                                                # only a new review shows the new answer
    assert new["decision"] == "within_reserve" and new["id"] != rep["id"]
    newq = biz.owner.get(f"{biz.base}/payment-reviews/{new['id']}/evidence-queue").json()
    assert not any(t["tier"] == "blocking" for t in newq["tasks"]) and newq["decision_on_record"] == "within_reserve"


def test_isolation_and_no_write_routes():
    biz, other = _setup(), _setup()
    rid = _review(biz)["id"]
    url = f"{biz.base}/payment-reviews/{rid}/evidence-queue"
    assert other.owner.get(url).status_code == 404
    assert other.owner.get(f"{other.base}/payment-reviews/{rid}/evidence-queue").status_code == 404
    assert biz.owner.post(url, json={}).status_code == 405
    from app.main import app
    methods = {m for r in app.routes if "evidence-queue" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert methods == {"GET"}

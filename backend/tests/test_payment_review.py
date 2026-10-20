"""Payment Review: hand-checked four-path results (pure engine), then the API: saving, replacement of a linked payable, access."""

import copy
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db import get_engine
from app.modules.payment_review.engine import ReviewError, review
from app.modules.records.service import business_today
from tests.helpers import Biz

AS_OF = "2026-10-20"


def _ob(oid, direction, party, num, due, outstanding, disputed=False):
    return {"id": oid, "direction": direction, "party": party, "document_number": num, "due_date": due, "outstanding_minor": outstanding,
            "currency": "BDT", "disputed": disputed, "settlement_account_id": "bank"}


def _inputs(balance=16_000_000, freshness="fresh", obligations=None, transfers=()):
    return {"calc_version": "outlook-v1", "as_of": AS_OF, "base_currency": "BDT", "reserve": {"amount_minor": 8_000_000, "currency": "BDT"},
            "accounts": [
                {"id": "bank", "label": "Bank", "currency": "BDT", "usable_minor": balance, "freshness": freshness, "evidence_through": "2026-10-01" if freshness != "fresh" else AS_OF,
                 "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "an", "restriction_releases": []},
                {"id": "box", "label": "Cash box", "currency": "BDT", "usable_minor": None, "freshness": "no_anchor", "evidence_through": None,
                 "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": None, "restriction_releases": []}],
            "obligations": obligations if obligations is not None else CASE_OBS, "unmatched_transfers": list(transfers)}


# Hand-worked case: bank 16,000,000, reserve 8,000,000, as of 20 Oct 2026. Proposed: 6,000,000 on 23 Oct to Delta Supplies, linked to BILL-9.
#   R1 INV-1 +5,000,000 due 25 Oct | R2 INV-2 +3,000,000 overdue (expected 27 Oct, uncertain; cautious: left out) | R3 disputed: never counted
#   P1 RENT -2,000,000 22 Oct | P2 BILL-9 -6,000,000 23 Oct (REPLACED by the payment) | P3 -1,000,000 28 Oct
CASE_OBS = [_ob("R1", "receivable", "Eastline", "INV-1", "2026-10-25", 5_000_000), _ob("R2", "receivable", "Northwind", "INV-2", "2026-10-05", 3_000_000),
            _ob("R3", "receivable", "Rafi Stores", "INV-3", "2026-10-30", 4_000_000, disputed=True),
            _ob("P1", "payable", "Landlord", "RENT", "2026-10-22", 2_000_000), _ob("P2", "payable", "Delta Supplies", "BILL-9", "2026-10-23", 6_000_000),
            _ob("P3", "payable", "Transport Co", "TRN-1", "2026-10-28", 1_000_000)]
ADVERSE = {"receipt_delay_days": 10, "receipts_haircut_percent": 20, "extra_outflow_minor": 500_000, "extra_outflow_date": "2026-10-30"}


def _req(**kw):
    return {"account_id": "bank", "amount_minor": 6_000_000, "payment_date": "2026-10-23", "linked_payable_id": "P2", "payee": None,
            "adverse": dict(ADVERSE)} | kw


def path(res, key):
    return next(p for p in res["paths"] if p["key"] == key)


def test_exact_four_path_results():
    res = review(_inputs(), _req())
    assert [p["key"] for p in res["paths"]] == ["on_time", "receipts_delayed", "largest_missed", "adverse"]

    on = path(res, "on_time")  # 16M -2M (22 Oct) -6M (23 Oct) = 8M, exactly the reserve: not a breach
    assert on["minimum"] == {"date": "2026-10-23", "amount_minor": 8_000_000} and on["first_breach"] is None and on["shortfall_minor"] == 0
    assert on["status"] == "within_reserve"

    late = path(res, "receipts_delayed")  # INV-1 not until 8 Nov; 28 Oct bill takes it to 7M
    assert late["minimum"] == {"date": "2026-10-28", "amount_minor": 7_000_000}
    assert late["first_breach"] == {"date": "2026-10-28", "balance_minor": 7_000_000, "gap_minor": 1_000_000} and late["shortfall_minor"] == 1_000_000
    assert all(e["source_id"] != "R2" for e in late["events"])                       # overdue receipt not counted when delayed

    miss = path(res, "largest_missed")       # R1 (5,000,000, the largest eligible receipt) never arrives; R2 still comes 27 Oct
    assert all(e["source_id"] != "R1" for e in miss["events"]) and any(e["source_id"] == "R2" for e in miss["events"])
    assert miss["minimum"] == {"date": "2026-10-23", "amount_minor": 8_000_000} and miss["first_breach"] is None

    adv = path(res, "adverse")               # receipts 10 days later and 20% lower, extra 500,000 on 30 Oct
    assert [(e["date"], e["amount_minor"]) for e in adv["events"] if e["direction"] == "receivable"] == [("2026-11-04", 4_000_000), ("2026-11-06", 2_400_000)]
    assert adv["first_breach"] == {"date": "2026-10-28", "balance_minor": 7_000_000, "gap_minor": 1_000_000}
    assert adv["minimum"] == {"date": "2026-10-30", "amount_minor": 6_500_000} and adv["shortfall_minor"] == 1_500_000

    assert res["decision"] == "reserve_shortfall" and res["arithmetic_state"] == "reserve_shortfall" and res["evidence_state"] == "complete"
    assert res["blockers"] == [] and "does not approve" in res["authorization"]


def test_linked_payable_is_replaced_not_double_counted():
    linked = path(review(_inputs(), _req()), "on_time")
    assert [e["source_id"] for e in linked["events"]].count("P2") == 1               # the payment carries the bill's id; no second event
    assert sum(e["amount_minor"] for e in linked["events"] if e["date"] == "2026-10-23") == -6_000_000
    unlinked = review(_inputs(), _req(linked_payable_id=None))                        # same payment, bill left in: counted twice
    on = path(unlinked, "on_time")
    assert on["first_breach"] == {"date": "2026-10-23", "balance_minor": 2_000_000, "gap_minor": 6_000_000}
    partial = path(review(_inputs(), _req(amount_minor=4_000_000)), "on_time")        # 4,000,000 of 6,000,000: remainder stays on 23 Oct
    rest = [e for e in partial["events"] if "remainder" in e["label"]]
    assert [(e["date"], e["amount_minor"]) for e in rest] == [("2026-10-23", -2_000_000)]
    assert partial["minimum"]["amount_minor"] == 8_000_000


def test_outflows_before_inflows_on_the_same_day():
    obs = [_ob("R", "receivable", "Eastline", "INV", "2026-10-22", 5_000_000)]
    res = review(_inputs(balance=9_000_000, obligations=obs), _req(amount_minor=3_000_000, payment_date="2026-10-22", linked_payable_id=None))
    on = path(res, "on_time")   # the day closes at 11,000,000, but the 3,000,000 goes out first: lowest point 6,000,000
    assert on["first_breach"] == {"date": "2026-10-22", "balance_minor": 6_000_000, "gap_minor": 2_000_000}


def test_material_unknown_blocks_a_positive_result_but_never_hides_a_breach():
    # Without the 28 Oct bill every path holds at exactly the reserve (8,000,000), so a clean case exists.
    small = _req(adverse={"receipt_delay_days": 1, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None})
    no_p3 = [o for o in CASE_OBS if o["id"] != "P3"]
    ok = review(_inputs(obligations=no_p3), small)
    assert ok["decision"] == "within_reserve" and ok["evidence_state"] == "complete" and ok["blockers"] == []
    stale = review(_inputs(freshness="stale", obligations=no_p3), small)
    assert stale["decision"] == "more_information_needed" and stale["arithmetic_state"] == "within_reserve"
    assert [b["code"] for b in stale["blockers"]] == ["funding_balance_stale"] and stale["blockers"][0]["source_id"] == "bank"
    missing = review(_inputs(balance=None, freshness="no_anchor", obligations=no_p3), small)
    assert missing["decision"] == "more_information_needed" and missing["blockers"][0]["code"] == "funding_balance_missing"
    assert missing["arithmetic_state"] == "not_assessed" and all(p["minimum"] is None and p["status"] == "not_assessed" for p in missing["paths"])
    transfer = review(_inputs(transfers=[{"id": "tr-1"}], obligations=no_p3), small)
    assert transfer["decision"] == "more_information_needed" and transfer["blockers"][0]["source_id"] == "tr-1"
    both = review(_inputs(freshness="stale"), _req())                                 # known breach AND incomplete evidence
    assert both["decision"] == "reserve_shortfall" and both["evidence_state"] == "incomplete" and both["blockers"]
    disputed = review(_inputs(obligations=[_ob("P2", "payable", "Delta", "B", "2026-10-23", 6_000_000, disputed=True)]), small | {"linked_payable_id": "P2", "amount_minor": 6_000_000})
    assert "linked_payable_disputed" in [b["code"] for b in disputed["blockers"]]


def test_other_accounts_without_balance_are_left_out_and_noted():
    notes = review(_inputs(), _req())["notes"]
    assert {"code": "other_account", "message": "Cash box has no usable balance and is left out.", "source_id": "box"} in notes
    assert any(n["code"] == "uncertain_receipt" and n["source_id"] == "R2" for n in notes)


@pytest.mark.parametrize("change, message", [
    ({"amount_minor": 6_000_001}, "larger than the amount still outstanding"),
    ({"payment_date": "2026-10-20"}, "must be after"), ({"payment_date": "2027-03-01"}, "horizon"),
    ({"account_id": "nope"}, "not an account"), ({"linked_payable_id": "R1"}, "not an outstanding bill"),
    ({"adverse": {"receipt_delay_days": 0, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}}, "Define the adverse case"),
    ({"adverse": ADVERSE | {"extra_outflow_date": None}}, "needs a date"),
])
def test_invalid_requests_are_refused(change, message):
    with pytest.raises(ReviewError, match=message):
        review(_inputs(), _req(**change))


def test_deterministic_and_inputs_untouched():
    inputs, req = _inputs(), _req()
    before = copy.deepcopy(inputs)
    assert review(inputs, req) == review(inputs, req) and inputs == before


# --- API -----------------------------------------------------------------------------------------------


def _setup():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    bank = biz.account["ACC-BANK"]
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": biz.today.isoformat(), "balance_minor": 9_000_000,
                                                            "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    biz.ob = {}
    for direction, num, days, amount, cat in (("receivable", "INV-A", 5, 4_000_000, "sales"), ("payable", "BILL-A", 3, 2_000_000, "inventory_suppliers")):
        r = biz.owner.post(f"{biz.base}/obligations", json={
            "direction": direction, "party_name": "Party " + num, "document_number": num, "issue_date": biz.today.isoformat(),
            "due_date": (biz.today + timedelta(days=days)).isoformat(), "amount_minor": amount, "currency": "BDT", "category": cat,
            "source_reference": "book", "settlement_account_id": bank})
        assert r.status_code == 201, r.text
        biz.ob[num] = r.json()["id"]
    return biz


def _body(biz, **kw):
    return {"account_id": biz.account["ACC-BANK"], "amount_minor": 2_000_000, "payment_date": (biz.today + timedelta(days=4)).isoformat(),
            "linked_payable_id": biz.ob["BILL-A"], "payee": "Supplier Co",
            "adverse": {"receipt_delay_days": 3, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}} | kw


def test_report_is_saved_exact_and_verifiable():
    biz = _setup()
    r = biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz))
    assert r.status_code == 201, r.text
    rep = r.json()
    day4 = (biz.today + timedelta(days=4)).isoformat()
    res = rep["results"]
    # 9,000,000 opening; the 2,000,000 bill (due day 3) is replaced by the payment on day 4: lowest 7,000,000 (a double count would give 5,000,000).
    for p in res["paths"]:
        assert p["minimum"] == {"date": day4, "amount_minor": 7_000_000}, p["key"]
        assert p["first_breach"] == {"date": day4, "balance_minor": 7_000_000, "gap_minor": 1_000_000}
        assert biz.ob["BILL-A"] in [l["id"] for l in p["links"]]               # the linked bill is a source link
    assert res["decision"] == "reserve_shortfall" and rep["decision"] == "reserve_shortfall"
    assert biz.owner.get(f"{biz.base}/payment-reviews/{rep['id']}").json()["results"] == res          # saved, same figures
    listing = biz.owner.get(f"{biz.base}/payment-reviews").json()
    assert [x["id"] for x in listing] == [rep["id"]] and listing[0]["payment"]["amount_minor"] == 2_000_000
    v = biz.owner.get(f"{biz.base}/payment-reviews/{rep['id']}/verify").json()
    assert v["inputs_hash_matches"] and v["results_reproduce"]
    # Saved reports are frozen: paying the bill afterwards does not change the stored figures.
    biz.owner.post(f"{biz.base}/obligations/{biz.ob['BILL-A']}/allocations", json={"amount_minor": 2_000_000, "currency": "BDT", "allocation_date": biz.today.isoformat()})
    assert biz.owner.get(f"{biz.base}/payment-reviews/{rep['id']}").json()["results"] == res


def test_validation_permissions_isolation_audit_and_no_payment_route():
    biz, other = _setup(), _setup()
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, amount_minor=2_000_001)).status_code == 422
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, amount_minor=1.5)).status_code == 422             # no floats
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, payee="=HYPERLINK(1)")).status_code == 422
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, account_id=other.account["ACC-BANK"])).status_code == 422
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, linked_payable_id=other.ob["BILL-A"])).status_code == 422  # another business's bill
    assert biz.owner.post(f"{biz.base}/payment-reviews", json=_body(biz, surprise=1)).status_code == 422
    staff, bookkeeper = biz.member("staff"), biz.member("bookkeeper")
    assert staff.post(f"{biz.base}/payment-reviews", json=_body(biz)).status_code == 403
    assert staff.get(f"{biz.base}/payment-reviews").status_code == 403
    rep = bookkeeper.post(f"{biz.base}/payment-reviews", json=_body(biz))
    assert rep.status_code == 201
    rid = rep.json()["id"]
    for url in (f"{biz.base}/payment-reviews", f"{biz.base}/payment-reviews/{rid}", f"{biz.base}/payment-reviews/{rid}/verify"):
        assert other.owner.get(url).status_code == 404
    assert other.owner.get(f"{other.base}/payment-reviews/{rid}").status_code == 404
    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    created = [e for e in events if e["action"] == "paymentreview.create"]
    assert any(e["outcome"] == "success" and e["details"]["decision"] == "reserve_shortfall" for e in created)
    assert any(e["action"] == "paymentreview.run" and e["outcome"] == "denied" for e in events)
    assert not any("Supplier" in str(e["details"]) for e in created)                    # payee name is not copied into the audit trail
    from app.main import app
    methods = {(m, r.path) for r in app.routes if "payment-reviews" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert {m for m, _ in methods} == {"GET", "POST"} and not any(p.rsplit("/", 1)[-1] in {"pay", "execute", "initiate", "approve", "send"} for _, p in methods)
    with get_engine().connect() as conn:
        try:
            conn.execute(text("UPDATE payment_review SET decision = 'within_reserve' WHERE business_id = :b"), {"b": biz.id})
            raise AssertionError("payment_review must be append-only")
        except Exception as exc:  # noqa: BLE001
            assert "append-only" in str(exc)

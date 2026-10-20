"""Cash Gap Rescue Plan: hand-checked options (pure engine), then the API: permissions, saved plans, no mutation."""

import copy
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db import get_engine
from app.modules.funding.router import CATALOGUE
from app.modules.records.service import business_today
from app.modules.rescue.engine import PlanError, plan
from tests.helpers import Biz

AS_OF = "2026-10-20"
WC = next(p for p in CATALOGUE["products"] if p["id"] == "wc-line")


def _meta(oid, direction, party, num, category, due, outstanding, days_overdue=0, disputed=False):
    return {"id": oid, "direction": direction, "category": category, "currency": "BDT", "due_date": due, "outstanding_minor": outstanding, "disputed": disputed,
            "days_overdue": days_overdue, "document_number": num, "status": "open", "party": party, "settlement_account_id": "bank"}


def _inputs(od=3_000_000, freshness="fresh", permission=True, funding=True, adjustments=None, balance=10_000_000, meta_extra=()):
    meta = [_meta("R-od", "receivable", "Northwind", "INV-OD", "sales", "2026-10-05", od, 15), _meta("B-mkt", "payable", "Atlas Print", "BILL-M", "marketing", "2026-10-22", 1_500_000)] + list(meta_extra)
    outlook_obs = [{"id": m["id"], "direction": m["direction"], "party": m["party"], "document_number": m["document_number"], "due_date": m["due_date"],
                    "outstanding_minor": m["outstanding_minor"], "currency": "BDT", "disputed": m["disputed"], "settlement_account_id": "bank"} for m in meta]
    return {"review_id": "rev-1", "currency": "BDT", "obligations_meta": meta, "adjustments": adjustments or {},
            "permissions": [{"id": "perm-1", "obligation_id": "B-mkt", "agreed_due_date": "2026-11-20", "evidence_reference": "Email from Atlas Print, 15 Oct"}] if permission else [],
            "funding": {"run_id": "run-1", "catalogue_version": "catalogue-2026.10", "amount_minor": 2_000_000, "receiving_account_id": "bank", "eligible_ids": ["wc-line"], "products": [WC]} if funding else None,
            "request": {"account_id": "bank", "amount_minor": 3_000_000, "payment_date": "2026-10-26", "linked_payable_id": None, "payee": "Supplier",
                        "adverse": {"receipt_delay_days": 1, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}},
            "outlook": {"calc_version": "outlook-v1", "as_of": AS_OF, "base_currency": "BDT", "reserve": {"amount_minor": 8_000_000, "currency": "BDT"},
                        "accounts": [{"id": "bank", "label": "Bank", "currency": "BDT", "usable_minor": balance, "freshness": freshness, "evidence_through": AS_OF if freshness == "fresh" else "2026-10-01",
                                      "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "an", "restriction_releases": []}],
                        "obligations": outlook_obs, "unmatched_transfers": []}}


def opt(res, oid):
    return next(o for o in res["options"] if o["id"] == oid)


# Hand-worked case. Bank 10,000,000, reserve 8,000,000. A 1,500,000 marketing bill is due 22 Oct, a 3,000,000 invoice is 15 days
# overdue, and the owner proposes to pay 3,000,000 on 26 Oct. On the chosen (receipts delayed) path nothing arrives, so the lowest
# cash is 10,000,000 - 1,500,000 - 3,000,000 = 5,500,000 on 26 Oct: a 2,500,000 shortfall.
def test_baseline_and_exact_option_results():
    res = plan(_inputs(adjustments={"path": "receipts_delayed"}))
    assert res["target_path"] == "receipts_delayed" and res["reserve_minor"] == 8_000_000
    assert res["baseline"]["minimum"] == {"date": "2026-10-26", "amount_minor": 5_500_000} and res["baseline"]["remaining_shortfall_minor"] == 2_500_000
    assert res["baseline"]["first_breach"] == {"date": "2026-10-26", "balance_minor": 5_500_000, "gap_minor": 2_500_000}
    assert [o["id"] for o in res["options"]] == ["collect", "defer", "financing", "combination"]

    c = opt(res, "collect")      # 3,000,000 collected on 22 Oct: 10-22 lowest 8,500,000 (bill first), 10-26 lowest 11,500,000 - 3,000,000 = 8,500,000
    assert c["minimum"] == {"date": "2026-10-22", "amount_minor": 8_500_000} and c["first_breach"] is None and c["remaining_shortfall_minor"] == 0 and c["resolves"]
    assert (c["borrows"], c["financing_cost_minor"], c["effect_date"], c["improvement_minor"]) == (False, 0, "2026-10-22", 3_000_000)
    assert c["actions"][0]["amount_minor"] == 3_000_000 and c["actions"][0]["source"] == {"kind": "obligation", "id": "R-od"}

    d = opt(res, "defer")        # the bill moves to 20 Nov: 26 Oct lowest 7,000,000, then the bill falls due and cash reaches 5,500,000 on 20 Nov
    assert d["minimum"] == {"date": "2026-11-20", "amount_minor": 5_500_000} and d["remaining_shortfall_minor"] == 2_500_000 and not d["resolves"]
    assert d["first_breach"] == {"date": "2026-10-26", "balance_minor": 7_000_000, "gap_minor": 1_000_000}

    f = opt(res, "financing")    # wc-line 2,000,000: net 1,990,000 on 23 Oct, repays 2,059,178 on 22 Dec, cost 69,178
    assert f["financing_cost_minor"] == 69_178 and f["borrows"] and f["actions"][0]["terms"]["total_repayment_minor"] == 2_059_178
    assert f["minimum"] == {"date": "2026-12-22", "amount_minor": 5_430_822} and f["remaining_shortfall_minor"] == 2_569_178 and not f["resolves"]
    assert f["first_breach"] == {"date": "2026-10-26", "balance_minor": 7_490_000, "gap_minor": 510_000}

    k = opt(res, "combination")  # all three: lowest 8,430,822 on 22 Dec after the repayment
    assert k["minimum"] == {"date": "2026-12-22", "amount_minor": 8_430_822} and k["resolves"] and k["borrows"] and k["financing_cost_minor"] == 69_178
    assert len(k["actions"]) == 3 and k["effect_date"] == "2026-11-20"


def test_no_borrow_solution_is_recommended_with_reasons_and_dependencies():
    res = plan(_inputs(adjustments={"path": "receipts_delayed"}))
    assert res["recommendation"]["state"] == "recommended" and res["recommendation"]["option_id"] == "collect"
    why = " ".join(res["recommendation"]["reasons"])
    assert "does not borrow" in why.lower() and "1 unresolved dependency" in why and "also closes the gap" in why and "it borrows" in why   # the combination is ranked lower
    c = opt(res, "collect")
    assert c["recommended"] and not opt(res, "combination")["recommended"]
    assert c["dependencies"] == ["Northwind must actually pay 3000000 minor units by 2026-10-22 (invoice INV-OD is 15 day(s) overdue)."]
    assert any("Lender approval has not been requested" in x for x in opt(res, "financing")["dependencies"])
    assert any("documented (Email from Atlas Print, 15 Oct)" in x for x in opt(res, "defer")["dependencies"])
    assert "Nothing was changed in the ledger" in res["notice"]


def test_no_safe_recommendation_when_no_option_closes_the_gap():
    res = plan(_inputs(od=200_000, adjustments={"path": "receipts_delayed"}))
    assert [opt(res, i)["remaining_shortfall_minor"] for i in ("collect", "defer", "financing", "combination")] == [2_300_000, 2_500_000, 2_569_178, 2_369_178]
    assert not any(o["resolves"] for o in res["options"])
    rec = res["recommendation"]
    assert rec["state"] == "none_safe" and rec["option_id"] is None and not any(o["recommended"] for o in res["options"])
    assert "closest, \"Collect an overdue invoice\", still leaves 2300000 minor units short" in rec["reasons"][0]


def test_no_safe_recommendation_while_material_evidence_is_unverified():
    res = plan(_inputs(freshness="stale", adjustments={"path": "receipts_delayed"}))
    assert opt(res, "collect")["resolves"]                                          # the arithmetic would close the gap...
    assert res["recommendation"]["state"] == "none_safe" and "unverified" in res["recommendation"]["reasons"][0]   # ...but it cannot be called safe
    assert res["evidence_blockers"] and any("Not yet verified" in d for d in opt(res, "collect")["dependencies"])


def test_owner_can_adjust_assumptions_and_the_same_frozen_forecast_is_rerun():
    half = plan(_inputs(adjustments={"path": "receipts_delayed", "collect_percent": 50}))
    c = opt(half, "collect")            # 1,500,000 collected: 10-26 lowest 10,000,000 - 3,000,000 = 7,000,000
    assert c["actions"][0]["amount_minor"] == 1_500_000 and c["minimum"]["amount_minor"] == 7_000_000 and c["remaining_shortfall_minor"] == 1_000_000
    late = opt(plan(_inputs(adjustments={"path": "receipts_delayed", "collect_date": "2026-10-27"})), "collect")   # collected the day AFTER the payment
    assert late["minimum"] == {"date": "2026-10-26", "amount_minor": 5_500_000} and not late["resolves"]
    sooner = opt(plan(_inputs(adjustments={"path": "receipts_delayed", "deferral_date": "2026-10-30"})), "defer")
    assert sooner["actions"][0]["date"] == "2026-10-30" and sooner["minimum"]["amount_minor"] == 5_500_000
    again = plan(_inputs(adjustments={"path": "receipts_delayed"}))
    assert again["baseline"] == plan(_inputs(adjustments={"path": "receipts_delayed"}))["baseline"]   # baseline does not depend on the option chosen
    assert plan(_inputs())["target_path"] == "on_time"                                # default: the worst shortfall path (ties keep the first)


def test_unavailable_options_are_listed_with_reasons_and_never_simulated():
    res = plan(_inputs(permission=False, funding=False))
    assert [o["id"] for o in res["options"]] == ["collect"] and res["recommendation"]["option_id"] == "collect"      # no combination of a single option
    assert {u["id"]: u["reason"] for u in res["unavailable"]} == {
        "defer": "No discretionary bill has a documented permission to be paid later (Atlas Print BILL-M would need one).",
        "financing": "No Funding Match has been saved for this currency."}
    rent = _meta("B-rent", "payable", "Landlord", "RENT", "rent", "2026-10-25", 500_000)           # rent is never discretionary, even with a permission
    only = plan(_inputs(permission=False, funding=False, meta_extra=[rent]))
    assert "B-rent" not in str(only["unavailable"])
    none = plan(_inputs(od=0, permission=False, funding=False))
    assert none["options"] == [] and none["recommendation"]["state"] == "none_safe" and "No option is available" in none["recommendation"]["reasons"][0]
    disputed = _inputs(permission=False, funding=False)
    disputed["obligations_meta"][0]["disputed"] = True                                              # a disputed invoice cannot be collected
    assert [u["id"] for u in plan(disputed)["unavailable"]][0] == "collect"


@pytest.mark.parametrize("adj, msg", [
    ({"collect_obligation_id": "B-mkt"}, "overdue, undisputed invoice"), ({"collect_date": "2026-10-20"}, "after the review date"),
    ({"collect_date": "2027-03-01"}, "13-week"), ({"defer_obligation_id": "R-od"}, "discretionary bill"), ({"deferral_date": "2026-11-30"}, "no later than the documented 2026-11-20"),
    ({"deferral_date": "2026-10-22"}, "after the current due date"), ({"financing_product_id": "term-loan"}, "not eligible in the latest Funding Match"),
    ({"path": "adverse"}, None),
])
def test_invalid_adjustments_are_refused(adj, msg):
    if msg is None:
        assert plan(_inputs(adjustments=adj))["target_path"] == "adverse"        # a valid path that shows a shortfall
        return
    with pytest.raises(PlanError, match=msg):
        plan(_inputs(adjustments=adj))


def test_a_review_without_a_shortfall_has_nothing_to_rescue_and_inputs_are_untouched():
    with pytest.raises(PlanError, match="no reserve shortfall"):
        plan(_inputs(balance=30_000_000))
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    assert plan(inputs) == plan(inputs) and inputs == before


# --- API -----------------------------------------------------------------------------------------------


def _setup(overdue=3_000_000):
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    bank = biz.account["ACC-BANK"]
    day = lambda n: (biz.today + timedelta(days=n)).isoformat()
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": biz.today.isoformat(), "balance_minor": 10_000_000, "currency": "BDT",
                                                            "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201

    def make(direction, num, party, due, amount, category):
        r = biz.owner.post(f"{biz.base}/obligations", json={"direction": direction, "party_name": party, "document_number": num, "issue_date": day(-40), "due_date": day(due),
                                                            "amount_minor": amount, "currency": "BDT", "category": category, "source_reference": "book", "settlement_account_id": bank})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    biz.bill = make("payable", "BILL-M", "Atlas Print", 2, 1_500_000, "marketing")
    biz.rent = make("payable", "RENT", "Landlord", 40, 500_000, "rent")
    biz.inv = make("receivable", "INV-OD", "Northwind", -15, overdue, "sales")
    r = biz.owner.post(f"{biz.base}/payment-reviews", json={"account_id": bank, "amount_minor": 3_000_000, "payment_date": day(6), "payee": "Supplier",
                                                           "adverse": {"receipt_delay_days": 1, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}})
    assert r.status_code == 201, r.text
    biz.review = r.json()
    assert biz.review["decision"] == "reserve_shortfall"
    biz.url = f"{biz.base}/payment-reviews/{biz.review['id']}/rescue-plans"
    return biz


def _snapshot(biz):
    with get_engine().connect() as conn:
        return (conn.execute(text("SELECT count(*), coalesce(sum(amount_minor), 0) FROM ledger_transaction WHERE business_id = :b"), {"b": biz.id}).one(),
                conn.execute(text("SELECT count(*), coalesce(sum(amount_minor), 0), string_agg(due_date::text, ',' ORDER BY id) FROM obligation WHERE business_id = :b"), {"b": biz.id}).one(),
                conn.execute(text("SELECT count(*) FROM allocation WHERE business_id = :b"), {"b": biz.id}).scalar_one())


def test_permission_gate_then_saved_plan_without_any_mutation():
    biz = _setup()
    first = biz.owner.post(biz.url, json={"path": "receipts_delayed"})
    assert first.status_code == 201, first.text
    res = first.json()["results"]
    assert [u["id"] for u in res["unavailable"]] == ["defer", "financing"]            # no documented permission yet, no funding match saved
    c = next(o for o in res["options"] if o["id"] == "collect")
    assert c["resolves"] and res["recommendation"]["option_id"] == "collect" and c["minimum"]["amount_minor"] == 8_000_000   # exactly the reserve: the 500,000 rent on day 40 is the lowest point

    # Permission rules: only a discretionary, outstanding bill, with a later date, by someone who can write bills.
    perm = f"{biz.base}/obligations/{biz.bill}/deferral-permissions"
    new_date = (biz.today + timedelta(days=32)).isoformat()
    assert biz.owner.post(f"{biz.base}/obligations/{biz.rent}/deferral-permissions", json={"agreed_due_date": new_date, "evidence_reference": "x"}).status_code == 422
    assert biz.owner.post(f"{biz.base}/obligations/{biz.inv}/deferral-permissions", json={"agreed_due_date": new_date, "evidence_reference": "x"}).status_code == 422
    assert biz.owner.post(perm, json={"agreed_due_date": (biz.today + timedelta(days=1)).isoformat(), "evidence_reference": "x"}).status_code == 422   # earlier than due
    assert biz.owner.post(perm, json={"agreed_due_date": (biz.today + timedelta(days=300)).isoformat(), "evidence_reference": "x"}).status_code == 422  # too far
    assert biz.owner.post(perm, json={"agreed_due_date": new_date, "evidence_reference": "=1+1"}).status_code == 422
    staff = biz.member("staff")
    assert staff.post(perm, json={"agreed_due_date": new_date, "evidence_reference": "Email 12 Oct"}).status_code == 403
    r = biz.bookkeeper = biz.member("bookkeeper")
    ok = r.post(perm, json={"agreed_due_date": new_date, "evidence_reference": "Email from Atlas Print, 12 Oct"})
    assert ok.status_code == 201, ok.text
    permission_id = ok.json()["id"]
    assert staff.get(perm).status_code == 200 and len(staff.get(perm).json()) == 1

    before = _snapshot(biz)
    second = biz.owner.post(biz.url, json={"path": "receipts_delayed"}).json()
    res2 = second["results"]
    assert [u["id"] for u in res2["unavailable"]] == ["financing"] and [o["id"] for o in res2["options"]] == ["collect", "defer", "combination"]
    d = next(o for o in res2["options"] if o["id"] == "defer")
    assert d["actions"][0]["date"] == new_date and any("Email from Atlas Print, 12 Oct" in x for x in d["dependencies"])
    assert res2["recommendation"]["option_id"] == "collect"                              # collect needs 1 dependency, the combination 2
    assert _snapshot(biz) == before                                                      # proposals only: no ledger, bill, invoice, or payment changed

    # Voiding the permission removes the option again; the old saved plan is unchanged.
    v = biz.owner.post(f"{perm}/{permission_id}/void", json={"reason": "Supplier withdrew the agreement"})
    assert v.status_code == 200 and v.json()["voided"] is True
    assert biz.owner.post(f"{perm}/{permission_id}/void", json={"reason": "again"}).status_code == 409
    third = biz.owner.post(biz.url, json={"path": "receipts_delayed"}).json()
    assert [u["id"] for u in third["results"]["unavailable"]] == ["defer", "financing"]
    assert biz.owner.get(f"{biz.url}/{second['id']}").json()["results"] == res2
    assert [p["id"] for p in biz.owner.get(biz.url).json()] == [third["id"], second["id"], first.json()["id"]]
    ver = biz.owner.get(f"{biz.url}/{second['id']}/verify").json()
    assert ver["inputs_hash_matches"] and ver["results_reproduce"]


def test_financing_option_uses_the_latest_saved_funding_match():
    biz = _setup()
    rows = [(f"{(biz.today - timedelta(days=n)).isoformat()}", f"Sales {n}", 10_000_000, f"S{n}") for n in (60, 40, 20)]
    biz.post_rows("ACC-BANK", rows)
    with get_engine().connect() as conn:
        ids = [str(r.id) for r in conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b AND reference LIKE 'S%'"), {"b": biz.id})]
    assert biz.owner.post(f"{biz.base}/classifications", json={"items": [{"txn_id": i, "kind": "operating_receipt", "category": "sales"} for i in ids]}).status_code == 200
    m = biz.owner.post(f"{biz.base}/funding-matches", json={
        "amount_minor": 2_000_000, "currency": "BDT", "needed_by": (biz.today + timedelta(days=10)).isoformat(), "purpose": "working_capital",
        "receiving_account_id": biz.account["ACC-BANK"], "months_trading": 18, "security_available": ["guarantor"],
        "documents_available": ["bank_statements", "id", "trade_licence", "invoices", "wallet_history"]})
    assert m.status_code == 201 and m.json()["results"]["eligible"], m.text
    plan_res = biz.owner.post(biz.url, json={"path": "receipts_delayed"}).json()["results"]
    f = next(o for o in plan_res["options"] if o["id"] == "financing")
    assert f["borrows"] and f["financing_cost_minor"] > 0 and f["actions"][0]["source"]["id"] == m.json()["id"]
    assert [u["id"] for u in plan_res["unavailable"]] == ["defer"]
    assert biz.owner.post(biz.url, json={"path": "receipts_delayed", "financing_product_id": "ke-trade-line"}).status_code == 422


def test_validation_permissions_isolation_audit_and_no_action_routes():
    biz, other = _setup(), _setup()
    assert biz.owner.post(biz.url, json={"collect_percent": 0}).status_code == 422 and biz.owner.post(biz.url, json={"collect_percent": 1.5}).status_code == 422
    assert biz.owner.post(biz.url, json={"path": "sideways"}).status_code == 422 and biz.owner.post(biz.url, json={"surprise": 1}).status_code == 422
    assert biz.owner.post(biz.url, json={"collect_obligation_id": other.inv}).status_code == 422          # another business's invoice
    assert biz.owner.post(biz.url, json={"collect_date": biz.today.isoformat()}).status_code == 422
    staff = biz.member("staff")
    assert staff.post(biz.url, json={}).status_code == 403 and staff.get(biz.url).status_code == 403
    created = biz.member("bookkeeper").post(biz.url, json={})
    assert created.status_code == 201
    pid = created.json()["id"]
    for url in (biz.url, f"{biz.url}/{pid}", f"{biz.url}/{pid}/verify", f"{biz.base}/obligations/{biz.bill}/deferral-permissions"):
        assert other.owner.get(url).status_code == 404
    assert other.owner.post(biz.url, json={}).status_code == 404
    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    assert any(e["action"] == "rescue.run" and e["outcome"] == "success" for e in events) and any(e["action"] == "rescue.run" and e["outcome"] == "denied" for e in events)
    from app.main import app
    routes = {(m, r.path) for r in app.routes if "rescue-plans" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert {m for m, _ in routes} == {"GET", "POST"} and not any(p.rsplit("/", 1)[-1] in {"apply", "execute", "approve", "send", "pay"} for _, p in routes)
    with get_engine().connect() as conn:
        for sql in ("UPDATE rescue_plan SET recommendation = 'none_safe' WHERE business_id = :b", "DELETE FROM rescue_plan WHERE business_id = :b"):
            try:
                conn.execute(text(sql), {"b": biz.id})
                raise AssertionError("rescue_plan must be append-only")
            except Exception as exc:  # noqa: BLE001
                assert "append-only" in str(exc)
                conn.rollback()


def test_a_review_without_a_shortfall_cannot_be_rescued_through_the_api():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": biz.account["ACC-BANK"], "anchor_date": biz.today.isoformat(), "balance_minor": 30_000_000,
                                                            "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    rev = biz.owner.post(f"{biz.base}/payment-reviews", json={"account_id": biz.account["ACC-BANK"], "amount_minor": 1_000_000, "payment_date": (biz.today + timedelta(days=5)).isoformat(),
                                                              "adverse": {"receipt_delay_days": 1, "receipts_haircut_percent": 0, "extra_outflow_minor": 0, "extra_outflow_date": None}}).json()
    r = biz.owner.post(f"{biz.base}/payment-reviews/{rev['id']}/rescue-plans", json={})
    assert r.status_code == 422 and "no reserve shortfall" in r.text

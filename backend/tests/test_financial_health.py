"""Financial Health tests. Expected values: fixtures/financial_health_expected.md and the Northstar fixture."""

import uuid

import pytest

from app.fixtures.seed import seed
from tests.helpers import FIXTURE, Biz

MONTH = "2026-10"
CLASSES = {"T001": ("operating_receipt", "sales"), "T003": ("operating_receipt", "sales"),
           "T002": ("operating_payment", "inventory_suppliers"), "T008": ("operating_payment", "inventory_suppliers"),
           "T007": ("operating_payment", "rent"), "T006": ("operating_payment", "other_expense")}


@pytest.fixture(autouse=True)
def _seed():
    seed()


def classify(biz, items, client=None):
    return (client or biz.owner).post(f"{biz.base}/classifications", json={"items": items})


def fh(biz, month=MONTH, client=None):
    r = (client or biz.owner).get(f"{biz.base}/financial-health?month={month}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def ready() -> Biz:
    biz = Biz().post_fixture()
    assert biz.owner.post(f"{biz.base}/cash/transfers", json={"out_txn_id": biz.txn["T004"], "in_txn_id": biz.txn["T005"]}).status_code == 201
    r = classify(biz, [{"txn_id": biz.txn[ref], "kind": k, "category": c} for ref, (k, c) in CLASSES.items()])
    assert r.status_code == 200, r.text
    return biz


def test_totals_match_the_independent_fixture(ready):
    s = fh(ready)["summary"]
    expected = FIXTURE["expected"]
    assert s["operating_receipts_minor"] == expected["operating_receipts"]["amount_minor"] == 8_349_975
    assert s["operating_payments_minor"] == expected["operating_payments"]["amount_minor"] == 8_925_015
    assert s["net_operating_cash_minor"] == expected["net_operating_cash"]["amount_minor"] == -575_040
    assert s["margin"] == {"basis_points": -689, "available": True, "reason": None}
    assert [(c["category"], c["amount_minor"]) for c in s["payment_categories"]] == [
        ("rent", 4_500_000), ("inventory_suppliers", 4_075_015), ("other_expense", 350_000)]
    assert [(c["category"], c["amount_minor"]) for c in s["receipt_categories"]] == [("sales", 8_349_975)]
    assert {k: (v["in_minor"], v["out_minor"]) for k, v in s["excluded"].items()} == {"own_transfer": (1_000_000, 1_000_000)}
    assert s["complete"] is True and s["unclassified"]["count"] == 0 and s["transaction_count"] == 8


def test_every_number_links_to_its_source_records(ready):
    def drill(kind, category=None):
        q = f"month={MONTH}&kind={kind}" + (f"&category={category}" if category else "")
        r = ready.owner.get(f"{ready.base}/financial-health/transactions?{q}")
        assert r.status_code == 200
        return r.json()
    sales = drill("operating_receipt", "sales")
    assert {t["id"] for t in sales["transactions"]} == {ready.txn["T001"], ready.txn["T003"]} and sales["total_minor"] == 8_349_975
    assert all(t["source_batch_id"] and t["source_row_number"] >= 2 for t in sales["transactions"])
    assert {t["id"] for t in drill("operating_payment", "rent")["transactions"]} == {ready.txn["T007"]}
    assert drill("operating_payment")["total_minor"] == 8_925_015
    assert {t["id"] for t in drill("own_transfer")["transactions"]} == {ready.txn["T004"], ready.txn["T005"]}
    assert ready.owner.get(f"{ready.base}/financial-health/transactions?month={MONTH}&kind=revenue").status_code == 422


def test_financing_proceeds_are_not_revenue(ready):
    ready.post_rows("ACC-BANK", [("2026-10-14", "Loan disbursement BRAC", 10_000_000, f"LN-{uuid.uuid4().hex[:5]}")])
    loan = next(i for i in ready.owner.get(f"{ready.base}/classifications?month={MONTH}").json()["items"] if i["description"].startswith("Loan"))
    assert classify(ready, [{"txn_id": loan["id"], "kind": "operating_receipt", "category": "sales"}]).status_code == 200  # a mistake...
    assert fh(ready)["summary"]["operating_receipts_minor"] == 18_349_975
    assert classify(ready, [{"txn_id": loan["id"], "kind": "financing_inflow"}]).status_code == 200  # ...corrected
    s = fh(ready)["summary"]
    assert s["operating_receipts_minor"] == 8_349_975
    assert s["excluded"]["financing_inflow"]["in_minor"] == 10_000_000


def test_unclassified_records_make_the_summary_incomplete():
    biz = Biz().post_fixture()
    data = fh(biz)
    s = data["summary"]
    assert s["complete"] is False and s["operating_receipts_minor"] == 0
    assert (s["unclassified"]["count"], s["unclassified"]["in_minor"], s["unclassified"]["out_minor"]) == (8, 9_349_975, 9_925_015)
    assert "not yet classified" in data["what_changed"]
    assert s["margin"]["available"] is False and "No operating receipts" in s["margin"]["reason"]
    # Movements without declared coverage are reported for every account that has them.
    assert {c["label"] for c in data["coverage"] if c["uncovered_days"] > 0} >= {"Operating bank account", "Merchant mobile wallet", "Cash box"}


def test_trend_and_what_changed(ready):
    data = fh(ready)
    assert [t["month"] for t in data["trend"]] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert data["trend"][-1]["net_operating_cash_minor"] == -575_040 and data["trend"][-2]["operating_receipts_minor"] == 0
    assert data["what_changed"].startswith("Operating receipts rose by BDT 83,499.75 compared with 2026-09")
    assert "largest receipt category was sales" in data["what_changed"]
    assert data["calc_version"] == "fh-v1" and "not an audited profit and loss" in data["notice"]


def test_suggestions_come_from_invoice_payments():
    biz = Biz().post_fixture()
    ob = biz.owner.post(f"{biz.base}/obligations", json={
        "direction": "receivable", "party_name": "Eastline", "document_number": "INV-101", "issue_date": "2026-09-25",
        "due_date": "2026-10-02", "amount_minor": 6_500_000, "currency": "BDT", "category": "sales", "source_reference": "book"}).json()
    biz.owner.post(f"{biz.base}/obligations/{ob['id']}/allocations", json={
        "amount_minor": 6_500_000, "currency": "BDT", "allocation_date": "2026-10-02", "ledger_txn_id": biz.txn["T001"]})
    items = {i["id"]: i for i in biz.owner.get(f"{biz.base}/classifications?only_unclassified=true").json()["items"]}
    assert items[biz.txn["T001"]]["suggestion"] == {"kind": "operating_receipt", "category": "sales", "because": "Paid INV-101"}
    assert items[biz.txn["T002"]]["suggestion"] is None


@pytest.mark.parametrize("ref, kind, category, status", [
    ("T001", "operating_payment", "rent", 422),        # money in cannot be a payment
    ("T002", "operating_receipt", "sales", 422),       # money out cannot be a receipt
    ("T001", "operating_receipt", "rent", 422),        # rent is not a receipt category
    ("T001", "operating_receipt", None, 422),          # operating kinds need a category
    ("T001", "financing_inflow", "sales", 422),        # financing takes no category
    ("T002", "owner_contribution", None, 422),         # contribution is money in
    ("T001", "revenue", None, 422),                    # unknown kind
    ("T001", "other_non_operating", None, 200),        # allowed either sign
])
def test_classification_rules(ref, kind, category, status):
    biz = Biz().post_fixture()
    item = {"txn_id": biz.txn[ref], "kind": kind} | ({"category": category} if category else {})
    assert classify(biz, [item]).status_code == status


def test_classification_batch_is_all_or_nothing_and_guarded(ready):
    other = Biz().post_fixture()
    good = {"txn_id": ready.txn["T001"], "kind": "other_non_operating"}
    assert classify(ready, [good, {"txn_id": ready.txn["T002"], "kind": "operating_receipt", "category": "sales"}]).status_code == 422
    assert fh(ready)["summary"]["operating_receipts_minor"] == 8_349_975  # nothing changed
    assert classify(ready, [good, good]).status_code == 422                                   # duplicate ids
    assert classify(ready, []).status_code == 422
    assert classify(ready, [{"txn_id": ready.txn["T004"], "kind": "financing_outflow"}]).status_code == 409  # own transfer
    assert classify(ready, [{"txn_id": other.txn["T001"], "kind": "other_non_operating"}]).status_code == 404  # another business
    assert classify(ready, [good], client=ready.member("staff")).status_code == 403
    assert classify(ready, [good], client=ready.member("bookkeeper")).status_code == 200


def test_cross_business_isolation(ready):
    other = Biz()
    assert other.owner.get(f"{ready.base}/financial-health").status_code == 404
    assert other.owner.get(f"{ready.base}/financial-health/transactions?month={MONTH}&kind=operating_receipt").status_code == 404
    s = fh(other)["summary"]
    assert (s["operating_receipts_minor"], s["transaction_count"]) == (0, 0)  # nothing of the first business leaks in
    staff_view = fh(ready, client=ready.member("staff"))["summary"]
    assert staff_view["net_operating_cash_minor"] == -575_040


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "abc", "1999-12", "2999-01"])
def test_month_validation(ready, month):
    assert ready.owner.get(f"{ready.base}/financial-health?month={month}").status_code == 422

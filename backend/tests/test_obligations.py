"""Receivables and Payables tests."""

import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.fixtures.seed import seed
from app.main import app
from tests.helpers import Biz

AS_OF = "2026-10-16"


@pytest.fixture(autouse=True)
def _seed():
    seed()


def invoice(biz, client=None, **over):
    body = {"direction": "receivable", "party_name": "Eastline Stores", "document_number": f"INV-{uuid.uuid4().hex[:6]}",
            "issue_date": "2026-10-01", "due_date": "2026-10-20", "amount_minor": 5_000_000, "currency": "BDT",
            "category": "sales", "source_reference": "Invoice book p.12", **over}
    return (client or biz.owner).post(f"{biz.base}/obligations", json=body)


def bill(biz, **over):
    return invoice(biz, **{"direction": "payable", "party_name": "Delta Supplies", "category": "inventory_suppliers", **over})


def pay(biz, ob_id, amount, client=None, **over):
    return (client or biz.owner).post(f"{biz.base}/obligations/{ob_id}/allocations",
                                      json={"amount_minor": amount, "currency": "BDT", "allocation_date": "2026-10-10", **over})


def get(biz, ob_id):
    return biz.owner.get(f"{biz.base}/obligations/{ob_id}?as_of={AS_OF}").json()


def audit(biz, action):
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM audit_event WHERE business_id = :b AND action = :a ORDER BY seq"),
                                              {"b": biz.id, "a": action}).mappings()]


@pytest.fixture
def biz():
    return Biz()


# --- create and validate --------------------------------------------------------------------------------


def test_create_and_reuse_party(biz):
    a = invoice(biz).json()
    b = invoice(biz, party_name="  eastline   STORES ").json()  # same party, different spacing and case
    assert a["party"]["id"] == b["party"]["id"] and a["party"]["name"] == "Eastline Stores"
    assert (a["status"], a["outstanding_minor"], a["allocations"]) == ("open", 5_000_000, [])
    assert invoice(biz, document_number=a["document_number"].lower()).status_code == 409  # same number for same customer
    assert invoice(biz, party_name="Other Co", document_number=a["document_number"]).status_code == 201
    assert audit(biz, "obligation.create")[0]["details"]["amount_minor"] == 5_000_000


@pytest.mark.parametrize("over", [
    {"amount_minor": 0}, {"amount_minor": -1}, {"amount_minor": 12.5}, {"amount_minor": "100"}, {"amount_minor": 10**15 + 1},
    {"currency": "XYZ"}, {"category": "rent"}, {"due_date": "2026-09-30"}, {"due_date": "2032-01-01"},
    {"issue_date": "1999-12-31"}, {"issue_date": "2999-01-01", "due_date": "2999-02-01"},
    {"document_number": "=HYPERLINK(1)"}, {"document_number": "  "}, {"party_name": ""}, {"party_name": "x" * 121},
    {"source_reference": ""}, {"description": "​hidden"}, {"direction": "loan"}, {"surprise": True},
])
def test_invalid_obligations_are_rejected(biz, over):
    assert invoice(biz, **over).status_code == 422, over
    assert biz.owner.get(f"{biz.base}/obligations").json()["items"] == []


def test_settlement_account_must_belong_and_match_currency(biz):
    assert invoice(biz, settlement_account_id=Biz().account["ACC-BANK"]).status_code == 404
    ok = invoice(biz, settlement_account_id=biz.account["ACC-BANK"]).json()
    assert ok["settlement_account"]["label"] == "Operating bank account"


# --- the four required checks -------------------------------------------------------------------------


def test_partial_payment_then_settled_and_over_allocation_refused(biz):
    ob = invoice(biz).json()
    r = pay(biz, ob["id"], 2_000_000)
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["outstanding_minor"]) == ("partially_settled", 3_000_000)
    r = pay(biz, ob["id"], 3_000_001)
    assert r.status_code == 409 and r.json()["detail"]["outstanding_minor"] == 3_000_000
    assert any(e["outcome"] == "conflict" for e in audit(biz, "obligation.allocate"))
    r = pay(biz, ob["id"], 3_000_000)
    assert (r.json()["status"], r.json()["outstanding_minor"]) == ("settled", 0)
    assert pay(biz, ob["id"], 1).status_code == 409
    assert get(biz, ob["id"])["amount_minor"] == 5_000_000  # the original amount is untouched


def test_disputed_invoice(biz):
    ob = invoice(biz, due_date="2026-10-10").json()
    url = f"{biz.base}/obligations/{ob['id']}/dispute"
    assert biz.owner.post(url, json={"disputed": True}).status_code == 422  # reason required
    r = biz.owner.post(url, json={"disputed": True, "reason": "Customer says 20 cartons were damaged"})
    assert r.json()["disputed"] and r.json()["dispute_reason"].startswith("Customer")
    assert biz.owner.post(url, json={"disputed": True, "reason": "again"}).status_code == 409
    [dues] = biz.owner.get(f"{biz.base}/obligations/summary?as_of={AS_OF}").json()["customer_dues"]
    # Disputed money is shown on its own line and never inside the overdue buckets.
    assert (dues["disputed"], dues["overdue_1_30"], dues["total_outstanding"]) == (5_000_000, 0, 5_000_000)
    assert pay(biz, ob["id"], 1_000_000).status_code == 201  # a disputed invoice can still be partly paid
    r = biz.owner.post(url, json={"disputed": False})
    assert r.json()["disputed"] is False and r.json()["dispute_reason"] is None
    [dues] = biz.owner.get(f"{biz.base}/obligations/summary?as_of={AS_OF}").json()["customer_dues"]
    assert (dues["disputed"], dues["overdue_1_30"]) == (0, 4_000_000)


def test_reversal_restores_outstanding_without_changing_the_original(biz):
    ob = invoice(biz).json()
    payment = pay(biz, ob["id"], 2_000_000).json()["allocations"][0]
    url = f"{biz.base}/obligations/{ob['id']}/allocations/{payment['id']}/reverse"
    assert biz.owner.post(url, json={"reason": "Cheque bounced", "reversal_date": "2026-10-09"}).status_code == 422  # before payment
    r = biz.owner.post(url, json={"reason": "Cheque bounced", "reversal_date": "2026-10-12"})
    assert r.status_code == 201
    after = r.json()
    assert (after["status"], after["outstanding_minor"], after["amount_minor"]) == ("open", 5_000_000, 5_000_000)
    kinds = [(a["kind"], a["amount_minor"], a["reversed"]) for a in after["allocations"]]
    assert kinds == [("payment", 2_000_000, True), ("reversal", -2_000_000, False)]
    assert biz.owner.post(url, json={"reason": "again", "reversal_date": "2026-10-12"}).status_code == 409
    reversal_id = after["allocations"][1]["id"]
    assert biz.owner.post(f"{biz.base}/obligations/{ob['id']}/allocations/{reversal_id}/reverse",
                          json={"reason": "x", "reversal_date": "2026-10-12"}).status_code == 409


def test_concurrent_payments_cannot_over_allocate(biz):
    ob = invoice(biz, amount_minor=1_000_000).json()
    results = []

    def go():
        results.append(TestClient(app, cookies=biz.owner.cookies).post(
            f"{biz.base}/obligations/{ob['id']}/allocations",
            json={"amount_minor": 300_000, "currency": "BDT", "allocation_date": "2026-10-10"}).status_code)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201] * 3 + [409] * 5
    final = get(biz, ob["id"])
    assert (final["allocated_minor"], final["outstanding_minor"]) == (900_000, 100_000)


# --- linking to bank transactions ---------------------------------------------------------------------


def test_payment_linked_to_a_posted_bank_transaction():
    biz = Biz().post_fixture()
    a = invoice(biz, amount_minor=4_000_000).json()
    b = invoice(biz, amount_minor=4_000_000).json()
    t001 = biz.txn["T001"]  # +6,500,000 on 2026-10-02
    assert pay(biz, a["id"], 4_000_000, ledger_txn_id=t001).status_code == 422  # date must equal the transaction date
    assert pay(biz, a["id"], 4_000_000, ledger_txn_id=biz.txn["T002"], allocation_date="2026-10-03").status_code == 422  # money out
    assert pay(biz, a["id"], 4_000_000, ledger_txn_id=t001, allocation_date="2026-10-02").status_code == 201
    r = pay(biz, b["id"], 4_000_000, ledger_txn_id=t001, allocation_date="2026-10-02")
    assert r.status_code == 409 and r.json()["detail"]["available_minor"] == 2_500_000
    assert pay(biz, b["id"], 2_500_000, ledger_txn_id=t001, allocation_date="2026-10-02").status_code == 201
    # A supplier bill is paid by money out.
    supplier = bill(biz, amount_minor=3_275_025).json()
    r = pay(biz, supplier["id"], 3_275_025, ledger_txn_id=biz.txn["T002"], allocation_date="2026-10-03")
    assert r.status_code == 201 and r.json()["status"] == "settled"
    assert r.json()["allocations"][0]["ledger_txn"]["description"].startswith("Supplier payment")
    # b still has 1,500,000 outstanding, so the foreign transaction itself is what is refused.
    assert pay(biz, b["id"], 1, ledger_txn_id=Biz().post_fixture().txn["T001"], allocation_date="2026-10-02").status_code == 404


@pytest.mark.parametrize("amount, over", [
    (0, {}), (1.5, {}), ("100", {}), (100, {"currency": "USD"}), (100, {"allocation_date": "2999-01-01"}),
    (100, {"note": "=cmd"}), (100, {"extra": 1}),
])
def test_payment_validation(biz, amount, over):
    ob = invoice(biz).json()
    assert pay(biz, ob["id"], amount, **over).status_code == 422
    assert get(biz, ob["id"])["allocations"] == []


# --- cancel, summary ----------------------------------------------------------------------------------


def test_cancel_rules(biz):
    ob = invoice(biz).json()
    payment = pay(biz, ob["id"], 1_000_000).json()["allocations"][0]
    url = f"{biz.base}/obligations/{ob['id']}/cancel"
    assert biz.owner.post(url, json={"reason": "Issued twice"}).status_code == 409  # has net payments
    biz.owner.post(f"{biz.base}/obligations/{ob['id']}/allocations/{payment['id']}/reverse", json={"reason": "Refunded", "reversal_date": "2026-10-11"})
    r = biz.owner.post(url, json={"reason": "Issued twice"})
    assert r.json()["status"] == "cancelled"
    assert biz.owner.post(url, json={"reason": "again"}).status_code == 409
    assert pay(biz, ob["id"], 1).status_code == 409


def test_dues_buckets_and_upcoming_payables(biz):
    for due, amount in (("2026-10-18", 100), ("2026-10-10", 200), ("2026-09-01", 400), ("2026-11-30", 800)):
        invoice(biz, issue_date="2026-08-01", due_date=due, amount_minor=amount)
    disputed = invoice(biz, issue_date="2026-08-01", due_date="2026-10-01", amount_minor=1600).json()
    biz.owner.post(f"{biz.base}/obligations/{disputed['id']}/dispute", json={"disputed": True, "reason": "Wrong price"})
    settled = invoice(biz, amount_minor=3200).json()
    pay(biz, settled["id"], 3200)
    bill(biz, due_date="2026-10-20", amount_minor=50)
    bill(biz, due_date="2026-10-10", amount_minor=70)
    bill(biz, due_date="2026-12-31", amount_minor=90)
    s = biz.owner.get(f"{biz.base}/obligations/summary?as_of={AS_OF}").json()
    [d] = s["customer_dues"]
    assert (d["due_this_week"], d["overdue_1_30"], d["overdue_31_plus"], d["due_later"], d["disputed"], d["total_outstanding"]) == (
        100, 200, 400, 800, 1600, 3100)
    assert (d["overdue_count"], d["customers_overdue"]) == (2, 1)  # the disputed overdue invoice is not counted here
    [u] = s["upcoming_payables"]
    assert (u["total"], u["overdue_total"]) == (120, 70)
    assert [i["outstanding_minor"] for i in u["items"]] == [70, 50]
    assert biz.owner.get(f"{biz.base}/obligations/summary?as_of=2999-01-01").status_code == 422


# --- permissions, isolation, immutability ---------------------------------------------------------------


def test_staff_can_read_but_not_write(biz):
    ob = invoice(biz).json()
    staff = biz.member("staff")
    assert invoice(biz, client=staff).status_code == 403
    assert pay(biz, ob["id"], 1, client=staff).status_code == 403
    assert staff.get(f"{biz.base}/obligations").json()["items"][0]["id"] == ob["id"]
    assert pay(biz, ob["id"], 1, client=biz.member("bookkeeper")).status_code == 201


def test_other_business_cannot_see_or_touch(biz):
    ob = invoice(biz).json()
    other = Biz()
    assert other.owner.get(f"{biz.base}/obligations").status_code == 404
    assert other.owner.get(f"{other.base}/obligations/{ob['id']}").status_code == 404
    assert pay(other, ob["id"], 1).status_code == 404
    assert other.owner.post(f"{other.base}/obligations/{ob['id']}/dispute", json={"disputed": True, "reason": "x"}).status_code == 404


@pytest.mark.parametrize("stmt", [
    "UPDATE obligation SET amount_minor = amount_minor + 1",
    "UPDATE obligation SET due_date = due_date + 30",
    "DELETE FROM obligation",
    "UPDATE allocation SET amount_minor = 1",
    "DELETE FROM allocation",
])
def test_original_terms_and_allocations_are_immutable(biz, stmt):
    ob = invoice(biz).json()
    pay(biz, ob["id"], 100)
    with pytest.raises(DBAPIError):
        with get_engine().begin() as conn:
            conn.execute(text(stmt + " WHERE business_id = :b"), {"b": biz.id})
    assert get(biz, ob["id"])["amount_minor"] == 5_000_000

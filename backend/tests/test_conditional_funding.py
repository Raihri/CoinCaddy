"""Prompt 19: evidence-based receipt triggers, forecast branches, and idempotent review actions."""

from datetime import timedelta

from sqlalchemy import text

from app.db import get_engine
from app.modules.conditional_funding import router as conditional_router
from app.modules.records.service import business_today
from tests.helpers import Biz


def setup_plan_business():
    biz = Biz()
    today = business_today("Asia/Dhaka")
    bank = biz.account["ACC-BANK"]
    date_at = lambda offset: (today + timedelta(days=offset)).isoformat()
    biz.post_rows("ACC-BANK", [(date_at(-60), "Sales one", 10_000_000, "CF-S1"),
                               (date_at(-40), "Sales two", 10_000_000, "CF-S2"),
                               (date_at(-20), "Sales three", 10_000_000, "CF-S3")])
    with get_engine().connect() as conn:
        txn_ids = [str(r[0]) for r in conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b"), {"b": biz.id})]
    assert biz.owner.post(f"{biz.base}/classifications", json={"items": [{"txn_id": t, "kind": "operating_receipt", "category": "sales"} for t in txn_ids]}).status_code == 200
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": today.isoformat(), "balance_minor": 10_000_000,
                                                           "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Conditional test"}).status_code == 201
    invoice = biz.owner.post(f"{biz.base}/obligations", json={"direction": "receivable", "party_name": "Trigger Customer", "document_number": "INV-CF-19",
        "issue_date": date_at(-10), "due_date": date_at(7), "amount_minor": 2_000_000, "currency": "BDT", "category": "sales",
        "source_reference": "Conditional test invoice", "settlement_account_id": bank})
    assert invoice.status_code == 201, invoice.text
    match = biz.owner.post(f"{biz.base}/funding-matches", json={"amount_minor": 4_000_000, "currency": "BDT", "needed_by": date_at(7),
        "purpose": "working_capital", "receiving_account_id": bank, "months_trading": 18, "security_available": ["guarantor"],
        "documents_available": ["bank_statements", "id", "trade_licence", "invoices", "wallet_history"]})
    assert match.status_code == 201, match.text
    return biz, today, invoice.json(), match.json()


def create_plan(biz, today, invoice, match):
    url = f"{biz.base}/conditional-funding-plans"
    body = {"name": "October receipt backup", "obligation_id": invoice["id"], "expected_amount_minor": 1_000_000,
            "trigger_date": (today + timedelta(days=1)).isoformat(), "funding_match_run_id": match["id"], "product_id": match["results"]["eligible"][0]["id"]}
    response = biz.owner.post(url, json=body)
    assert response.status_code == 201, response.text
    return url, response.json()


def test_posted_linked_receipt_advances_plan_once_and_recalculates():
    biz, today, invoice, match = setup_plan_business()
    url, plan = create_plan(biz, today, invoice, match)
    assert plan["state"] == "awaiting_receipt"
    assert {s["branch"] for s in plan["snapshots"]} == {"receipt_expected", "backup_if_missed"}

    # A posted bank entry alone is not sufficient: the invoice allocation links the accepted source record to this receivable.
    biz.post_rows("ACC-BANK", [(today.isoformat(), "Customer receipt", 1_000_000, "CF-RECEIPT")])
    unallocated = biz.owner.post(f"{url}/{plan['id']}/evaluate", json={})
    assert unallocated.status_code == 200 and unallocated.json()["state"] == "awaiting_receipt"
    assert unallocated.json()["verified_amount_minor"] == 0
    with get_engine().connect() as conn:
        txn_id = str(conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b AND reference = 'CF-RECEIPT'"), {"b": biz.id}).scalar_one())
    assert biz.owner.post(f"{biz.base}/obligations/{invoice['id']}/allocations", json={"amount_minor": 1_000_000, "currency": "BDT",
        "allocation_date": today.isoformat(), "ledger_txn_id": txn_id, "note": "Linked to posted receipt"}).status_code == 201
    first = biz.owner.post(f"{url}/{plan['id']}/evaluate", json={})
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "receipt_recalculated"
    assert "settled_actual" in [s["branch"] for s in first.json()["snapshots"]]
    replay = biz.owner.post(f"{url}/{plan['id']}/evaluate", json={})
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert sum(e["type"] == "receipt_verified" for e in replay.json()["events"]) == 1


def test_missed_trigger_creates_only_review_state_and_repeat_is_idempotent(monkeypatch):
    biz, today, invoice, match = setup_plan_business()
    url, plan = create_plan(biz, today, invoice, match)
    trigger = today + timedelta(days=1)
    monkeypatch.setattr(conditional_router, "_today", lambda db, business_id: trigger + timedelta(days=1))

    missed = biz.owner.post(f"{url}/{plan['id']}/evaluate", json={})
    assert missed.status_code == 200, missed.text
    assert missed.json()["state"] == "backup_review_pending"
    assert {s["branch"] for s in missed.json()["snapshots"]} >= {"missed_no_borrow", "missed_with_backup"}
    assert any(e["type"] == "trigger_missed" and e["details"]["notification_only"] for e in missed.json()["events"])

    replay = biz.owner.post(f"{url}/{plan['id']}/evaluate", json={})
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert sum(e["type"] == "trigger_missed" for e in replay.json()["events"]) == 1
    reviewed = biz.owner.post(f"{url}/{plan['id']}/review", json={"decision": "proceed"})
    assert reviewed.status_code == 200 and reviewed.json()["state"] == "backup_reviewed"
    assert reviewed.json()["backup_decision"] == "proceed"
    retry = biz.owner.post(f"{url}/{plan['id']}/review", json={"decision": "proceed"})
    assert retry.status_code == 200 and retry.json()["replayed"] is True


def test_only_owner_can_create_or_review_and_staff_cannot_read():
    biz, today, invoice, match = setup_plan_business()
    url = f"{biz.base}/conditional-funding-plans"
    body = {"name": "Owner only", "obligation_id": invoice["id"], "expected_amount_minor": 1_000_000,
            "trigger_date": (today + timedelta(days=1)).isoformat(), "funding_match_run_id": match["id"], "product_id": match["results"]["eligible"][0]["id"]}
    bookkeeper = biz.member("bookkeeper")
    staff = biz.member("staff")
    assert bookkeeper.post(url, json=body).status_code == 403
    assert staff.get(url).status_code == 403
    assert staff.post(url, json=body).status_code == 403

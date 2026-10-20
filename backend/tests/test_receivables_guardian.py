"""Prompt 18: aging, disputed holds, verified contacts, internal drafts, approval and sandbox evidence."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_engine
from app.modules.executor import adapters
from app.modules.guardian.engine import normalize_address
from app.modules.records.service import business_today
from mock_services.app import app as mock_app
from tests.helpers import Biz


def setup_guardian():
    biz = Biz()
    today = business_today("Asia/Dhaka")
    day = lambda n: (today + timedelta(days=n)).isoformat()

    def invoice(number, customer, due=-12, amount=4_500_000):
        r = biz.owner.post(f"{biz.base}/obligations", json={
            "direction": "receivable", "party_name": customer, "document_number": number,
            "issue_date": day(-90), "due_date": day(due), "amount_minor": amount,
            "currency": "BDT", "category": "sales", "source_reference": "synthetic test invoice"})
        assert r.status_code == 201, r.text
        return r.json()

    return biz, day, invoice


def contact(biz, party_id, address="billing@example.com", decision="verify"):
    base = f"{biz.base}/guardian"
    r = biz.owner.post(f"{base}/customers/{party_id}/contacts", json={"channel": "email", "address": address})
    assert r.status_code == 201, r.text
    c = r.json()
    if decision:
        body = ({"decision": "verify", "method": "called_known_contact", "reference": "Known number on customer file"}
                if decision == "verify" else {"decision": "reject", "reason": "Address belongs to someone else"})
        r = biz.owner.post(f"{base}/contacts/{c['id']}/review", json=body)
        assert r.status_code == 200, r.text
        c = r.json()
    return c


def test_contact_digest_is_keyed_and_business_scoped():
    masked, first = normalize_address("email", "Billing@Example.com", "business-a", "private-key")
    _, same = normalize_address("email", "billing@example.com", "business-a", "private-key")
    _, other_business = normalize_address("email", "billing@example.com", "business-b", "private-key")
    _, other_key = normalize_address("email", "billing@example.com", "business-a", "another-key")
    assert masked == "b***@example.com"
    assert first == same and first != other_business and first != other_key
    assert "billing" not in first and len(first) == 64


def test_aging_suggestions_disputed_invoice_and_staff_isolation():
    biz, day, invoice = setup_guardian()
    old = invoice("INV-OLD", "Old Customer", due=-65, amount=2_000_000)
    recent = invoice("INV-RECENT", "Recent Customer", due=-3, amount=3_000_000)
    disputed = invoice("INV-DISPUTED", "Disputed Customer", due=-40, amount=1_000_000)
    future = invoice("INV-FUTURE", "Future Customer", due=7, amount=500_000)
    r = biz.owner.post(f"{biz.base}/obligations/{disputed['id']}/dispute", json={"disputed": True, "reason": "Goods not received"})
    assert r.status_code == 200, r.text

    root = f"{biz.base}/guardian"
    overview = biz.owner.get(root)
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["rules_version"] == "guardian-v1"
    aging = data["aging"][0]
    assert aging["buckets"]["61-90"]["amount_minor"] == 2_000_000
    assert aging["buckets"]["1-30"]["amount_minor"] == 3_000_000
    assert aging["buckets"]["current"]["amount_minor"] == 500_000
    assert aging["disputed"]["amount_minor"] == 1_000_000
    assert [s["customer"] for s in data["suggestions"]] == ["Old Customer", "Recent Customer"]
    assert {i["id"] for c in data["customers"] for i in c["invoices"]} == {old["id"], recent["id"], disputed["id"], future["id"]}
    assert biz.owner.post(f"{root}/drafts", json={"obligation_id": disputed["id"], "channel": "email"}).status_code == 422
    assert biz.owner.post(f"{root}/drafts", json={"obligation_id": future["id"], "channel": "email"}).status_code == 422
    staff = biz.member("staff")
    assert staff.get(root).status_code == 403
    assert staff.get(f"{root}/drafts").status_code == 403
    other = Biz()
    assert other.owner.get(f"{other.base}/guardian/drafts/{old['id']}").status_code == 404
    assert recent["id"] != old["id"]


def test_rejected_and_unverified_contacts_block_drafting_and_a_new_verified_contact_works():
    biz, _, invoice = setup_guardian()
    inv = invoice("INV-108", "Eastline Stores")
    root = f"{biz.base}/guardian"
    party_id = inv["party"]["id"]
    rejected = contact(biz, party_id, decision="reject")
    assert rejected["status"] == "rejected" and "@example.com" in rejected["address_masked"]
    body = {"obligation_id": inv["id"], "channel": "email"}
    r = biz.owner.post(f"{root}/drafts", json=body)
    assert r.status_code == 422 and "rejected" in r.text
    unverified = contact(biz, party_id, address="new@example.com", decision=None)
    assert unverified["status"] == "unverified"
    assert biz.owner.post(f"{root}/drafts", json=body).status_code == 422
    owner_review = biz.owner.post(f"{root}/contacts/{unverified['id']}/review", json={
        "decision": "verify", "method": "called_known_contact", "reference": "Customer phone on file"})
    assert owner_review.status_code == 200
    draft = biz.owner.post(f"{root}/drafts", json=body)
    assert draft.status_code == 201, draft.text
    d = draft.json()
    assert d["state"] == "draft" and d["proposal_id"] is None and d["internal_only"]
    assert "INV-108" in d["message"] and "Eastline Stores" in d["message"]
    assert d["contact"]["id"] == unverified["id"]
    assert biz.owner.get(f"{biz.base}/obligations/{inv['id']}").json()["outstanding_minor"] == 4_500_000
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM action_proposal WHERE business_id = :b"), {"b": biz.id}).scalar_one() == 0


def test_approval_and_sandbox_outcome_do_not_settle_invoice(monkeypatch):
    biz, _, invoice = setup_guardian()
    inv = invoice("INV-108", "Eastline Stores")
    root = f"{biz.base}/guardian"
    contact(biz, inv["party"]["id"])
    draft = biz.owner.post(f"{root}/drafts", json={"obligation_id": inv["id"], "channel": "email"}).json()
    r = biz.owner.post(f"{root}/drafts/{draft['id']}/propose", json={})
    assert r.status_code == 201, r.text
    p = r.json()["proposal"]
    assert p["state"] == "pending" and p["payload"]["contact"]["address_masked"] == "b***@example.com"
    assert biz.owner.post(f"{root}/drafts/{draft['id']}/propose", json={}).status_code == 409
    run_body = {"proposal_id": p["id"], "payload_sha256": p["payload_sha256"],
                "idempotency_key": "guardian-unapproved-0001", "confirm_sandbox": True}
    assert biz.owner.post(f"{biz.base}/sandbox/executions", json=run_body).status_code == 409

    decision = biz.owner.post(f"{biz.base}/approvals/{p['id']}/decision", json={
        "decision": "approve", "payload_sha256": p["payload_sha256"]})
    assert decision.status_code == 200, decision.text
    release = biz.owner.post(f"{biz.base}/approvals/{p['id']}/release", json={"payload_sha256": p["payload_sha256"]})
    assert release.status_code == 200, release.text
    monkeypatch.setattr(adapters, "mock_client", lambda: TestClient(mock_app))
    run = biz.owner.post(f"{biz.base}/sandbox/executions", json=run_body)
    assert run.status_code == 201, run.text
    assert run.json()["outcome"] == "succeeded"
    updated = biz.owner.get(f"{root}/drafts/{draft['id']}").json()
    assert updated["proposal_id"] == p["id"] and updated["sent_in_sandbox"]
    assert updated["execution"]["reference"].startswith("SBX-RMD-")
    assert biz.owner.get(f"{biz.base}/obligations/{inv['id']}").json()["outstanding_minor"] == 4_500_000
    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    assert any(e["action"] == "guardian.draft_create" for e in events)
    assert all("billing@example.com" not in str(e["details"]) and draft["message"] not in str(e["details"]) for e in events)

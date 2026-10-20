"""Funding Passport snapshots disclose only owner-selected, immutable fields through the sandbox workflow."""
from datetime import timedelta
import httpx
import pytest
from fastapi.testclient import TestClient

import pytest
from sqlalchemy import text

from app.contracts.business_time import utc_now
from app.db import get_engine
from app.modules.approvals import router as approvals_router
from tests.helpers import PASSWORD
from tests.test_approval_workflow import VERIFY, _approve, _release, _setup
from mock_services.app import app as mock_app
from app.modules.executor import adapters
from tests.test_sandbox_executor import _key, _run


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    calls = []
    def client():
        c = TestClient(mock_app)
        original = c.post
        c.post = lambda path, **kw: (calls.append((path, kw["headers"]["Idempotency-Key"])), original(path, **kw))[1]
        return c
    monkeypatch.setattr(adapters, "mock_client", client)
    return calls


def _make_packet(biz, **overrides):
    return biz.owner.post(f"{biz.base}/funding-passport/packets", json={"recipient": "sandbox_lender_a",
        "selected_fields": ["business_name"], "document_ids": [], "expires_in_days": 7} | overrides)


def test_owner_builds_versioned_packet_with_only_selected_fields_and_records_disclosure(sandbox):
    biz = _setup()
    packet = _make_packet(biz)
    assert packet.status_code == 201, packet.text
    snapshot = packet.json()
    assert snapshot["version"] == "passport-v1" and set(snapshot["packet"]) == {"business_name"}
    assert "cash_summary" not in snapshot["packet"] and "supplier_bills" not in snapshot["packet"]
    proposal = biz.owner.post(biz.url, json={"kind": "funding_packet_share", "packet_id": snapshot["id"]})
    assert proposal.status_code == 201, proposal.text
    p = proposal.json()
    assert p["tier"] == "T3" and p["second_reviewer_required"] and p["payload"]["packet"] == snapshot["packet"]
    staff = biz.member("staff")
    assert _approve(biz, staff, p, password=PASSWORD, verification=VERIFY | {"checked_on": biz.today.isoformat()}).status_code == 403
    reviewer = biz.member("owner")
    assert _approve(biz, reviewer, p, password=PASSWORD, verification=VERIFY | {"checked_on": biz.today.isoformat()}).status_code == 200
    released = _release(biz, biz.owner, p)
    assert released.status_code == 200, released.text
    run = _run(biz, biz.owner, released.json(), key=_key())
    assert run.status_code == 201, run.text
    assert run.json()["outcome"] == "succeeded" and run.json()["adapter"] == "packet_share"
    assert sandbox[-1][0] == "/sandbox/packets"
    with get_engine().connect() as conn:
        saved = conn.execute(text("SELECT packet, packet_sha256 FROM funding_passport_packet WHERE id = :i"), {"i": snapshot["id"]}).mappings().one()
    assert saved["packet"] == snapshot["packet"] and saved["packet_sha256"] == snapshot["packet_sha256"]


def test_expired_packet_cannot_be_proposed(monkeypatch):
    biz = _setup()
    packet = _make_packet(biz, expires_in_days=1).json()
    with get_engine().connect() as conn:
        expires = conn.execute(text("SELECT expires_at FROM funding_passport_packet WHERE id = :i"), {"i": packet["id"]}).scalar_one()
    monkeypatch.setattr(approvals_router, "utc_now", lambda: expires + timedelta(seconds=1))
    r = biz.owner.post(biz.url, json={"kind": "funding_packet_share", "packet_id": packet["id"]})
    assert r.status_code == 422 and "expired" in r.text.lower()


def test_packet_expiry_and_document_selection_validation():
    biz = _setup()
    docs = f"{biz.base}/business-documents"
    doc = biz.owner.post(docs, json={"kind": "id", "reference": "Scanned copy in office drive"})
    assert doc.status_code == 201, doc.text
    packet = _make_packet(biz, document_ids=[doc.json()["id"]]).json()
    assert packet["packet"]["selected_evidence"][0]["kind"] == "id"
    assert "id" not in packet["packet"]["selected_evidence"][0] and "where_kept" not in packet["packet"]["selected_evidence"][0]
    voided = biz.owner.post(f"{docs}/{doc.json()['id']}/void", json={"reason": "Wrong evidence entry"})
    assert voided.status_code == 200
    invalid = _make_packet(biz, document_ids=[doc.json()["id"]])
    assert invalid.status_code == 422 and "voided" in invalid.text.lower()
    bad_days = _make_packet(biz, expires_in_days=31)
    assert bad_days.status_code == 422

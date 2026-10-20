"""Sandbox Action Executor: approved proposals run once through separate mock adapters, with every gate checked again.

Covers the four required cases (an approved action, a denied unapproved action, a blocked fraud-held action, an
idempotent retry), plus expiry, tampering, a sandbox rejection, a lost answer, the packet-sharing adapter, redaction,
validation, access, isolation, and the database guards.
"""

import hashlib
import re
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.contracts.business_time import utc_now
from app.db import get_engine
from app.modules.executor import adapters
from app.modules.executor import router as executor_router
from app.modules.executor.service import mock_key
from mock_services.app import app as mock_app
from tests.helpers import PASSWORD, Biz
from tests.test_approval_workflow import VERIFY, _approve, _payment, _release, _reminder, _tamper, _setup as approvals_setup
from tests.test_financing_readiness import _setup as readiness_setup

MESSAGE = "Dear Eastline Stores, invoice INV-108 is overdue. Please arrange payment."
REF = re.compile(r"^SBX-(RMD|PAY|APP|PKT)-[0-9A-F]{10}$")


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    """Point every adapter at the mock-service app in this process (the same app that runs on port 8100)."""
    calls = []

    def client():
        c = TestClient(mock_app)
        original = c.post
        c.post = lambda path, **kw: (calls.append((path, kw["headers"]["Idempotency-Key"])), original(path, **kw))[1]
        return c
    monkeypatch.setattr(adapters, "mock_client", client)
    return calls


def _mock_seen(key: str) -> list[dict]:
    return TestClient(mock_app).get(f"/sandbox/requests?key={key}").json()["items"]


def _key() -> str:
    return f"exec-{uuid.uuid4()}"


def _run(biz, client, p, key=None, **kw):
    body = {"proposal_id": p["id"], "payload_sha256": p["payload_sha256"], "idempotency_key": key or _key(), "confirm_sandbox": True} | kw
    return client.post(f"{biz.base}/sandbox/executions", json=body)


def _log(biz, client=None, **params) -> list[dict]:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = (client or biz.owner).get(f"{biz.base}/sandbox/executions?limit=100&{q}")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _events(biz) -> list[dict]:
    return biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]


def _released_reminder(biz, client=None, **kw) -> dict:
    p = _reminder(biz, client, **kw)
    assert p.status_code == 201, p.text
    p = p.json()
    assert _approve(biz, biz.owner, p).status_code == 200
    r = _release(biz, biz.owner, p)
    assert r.status_code == 200, r.text
    return r.json()


def _destination(biz, client, account_number="1234 5678 9012", **kw):
    fraud = f"{biz.base}/fraud"
    party = next(p for p in biz.owner.get(f"{fraud}/payee-destinations").json()["parties"] if p["name"] == "Delta Supplies")["party_id"]
    r = client.post(f"{fraud}/payee-destinations", json={"party_id": party, "kind": "bank", "institution": "Dutch-Bangla Bank",
                                                         "account_number": account_number, "change_source": "onboarding"} | kw)
    assert r.status_code == 201, r.text
    return r.json()


def _released_payment(biz, owner2, amount=5_000_000, **kw) -> dict:
    p = _payment(biz, amount_minor=amount, **kw)
    assert p.status_code == 201, p.text
    p = p.json()
    a = _approve(biz, owner2, p, password=PASSWORD, verification=VERIFY | {"checked_on": biz.today.isoformat()})
    assert a.status_code == 200, a.text
    r = _release(biz, biz.owner, p)
    assert r.status_code == 200, r.text
    return r.json()


def _checksum(biz) -> str:
    with get_engine().connect() as conn:
        rows = [conn.execute(text(f"SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) FROM {t} t WHERE business_id = :b"),
                             {"b": biz.id}).scalar_one()
                for t in ("ledger_transaction", "obligation", "allocation", "action_proposal", "action_decision")]
    return "/".join(rows)


# --- required case 1: an approved action -----------------------------------------------------------------------


def test_approved_reminder_runs_once_through_its_adapter_and_is_logged_redacted(sandbox):
    biz = approvals_setup()
    keeper = biz.member("bookkeeper")
    p = _released_reminder(biz, keeper)
    assert p["state"] == "executable" and p["executed"] is False and p["execution"] is None
    before = _checksum(biz)

    r = _run(biz, biz.owner, p)
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["outcome"] == "succeeded" and e["final"] and e["replayed"] is False and e["sandbox"] is True
    assert e["adapter"] == "reminder_send" and e["endpoint"] == "/sandbox/reminders" and REF.match(e["reference"]) and e["reference"].startswith("SBX-RMD-")
    assert e["result"]["delivered"] is False and "no email was sent" in e["result"]["note"] and e["attempts"] == 1
    # Exactly one call, to the reminder endpoint, with the key derived from the proposal.
    mk = mock_key(biz.id, p["id"], p["payload_sha256"])
    assert sandbox == [("/sandbox/reminders", mk)] and _mock_seen(mk)[0]["times_seen"] == 1
    # The request log is redacted: the message is kept only as its hash and length.
    req = e["request"]
    assert "message" not in req and req["message_sha256"] == hashlib.sha256(MESSAGE.encode()).hexdigest() and req["message_length"] == len(MESSAGE)
    assert req["customer"] == "Eastline Stores" and req["reference"] == "INV-108" and req["amount_minor"] == 4_500_000
    assert [x["event"] for x in e["log"]] == ["dispatched", "succeeded"] and all(x["at"] for x in e["log"])
    assert {c["code"] for c in e["log"][0]["checks"]} >= {"released", "business_scope", "approved", "not_expired", "payload_integrity", "records_current", "adapter"}
    # The proposal shows it was executed; the invoice is not marked paid and no record changed.
    got = biz.owner.get(f"{biz.url}/{p['id']}").json()
    assert got["executed"] is True and got["execution"]["reference"] == e["reference"] and got["state"] == "executable"
    inv = biz.owner.get(f"{biz.base}/obligations/{biz.inv}").json()
    assert inv["outstanding_minor"] == 4_500_000 and inv["status"] in ("open", "partially_settled")
    assert _checksum(biz) == before
    # The bookkeeper can read the log but cannot run anything.
    assert [x["event"] for x in _log(biz, keeper)] == ["succeeded", "dispatched"]
    assert biz.owner.get(f"{biz.base}/sandbox/executions/{e['id']}").json()["reference"] == e["reference"]
    ev = next(x for x in _events(biz) if x["action"] == "executor.execute")
    assert ev["outcome"] == "success" and ev["details"]["reference"] == e["reference"] and "Please arrange" not in str(ev["details"])


def test_approved_payment_and_application_use_their_own_adapters(sandbox):
    biz = approvals_setup()
    owner2 = biz.member("owner")
    _destination(biz, biz.owner)
    p = _released_payment(biz, owner2)
    r = _run(biz, biz.owner, p)
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["adapter"] == "payment_initiate" and e["reference"].startswith("SBX-PAY-") and e["result"]["money_moved"] is False
    assert e["request"]["destination"] == {"kind": "bank", "institution": "Dutch-Bangla Bank", "last4": "9012"}
    assert e["request"]["amount_minor"] == 5_000_000 and e["request"]["payee"] == "Delta Supplies"
    assert "fraud_hold" in {c["code"] for c in e["log"][0]["checks"]} and "destination_on_file" in {c["code"] for c in e["log"][0]["checks"]}
    assert biz.owner.get(f"{biz.base}/obligations/{biz.bill}").json()["outstanding_minor"] == 6_000_000   # nothing is allocated

    rb = readiness_setup()
    rb.url = f"{rb.base}/approvals"
    assert rb.owner.post(f"{rb.base}/cash/coverage", json={"account_id": rb.account["ACC-BANK"], "period_start": rb.day(100), "period_end": rb.today.isoformat(),
                                                           "source_kind": "statement", "note": "Statements"}).status_code == 201
    for doc in ({"kind": "id", "reference": "Passport copy, office drive"},
                {"kind": "trade_licence", "reference": "Framed, front office", "issued_on": "2024-01-15", "expires_on": (rb.today + timedelta(days=200)).isoformat()}):
        assert rb.owner.post(f"{rb.base}/business-documents", json=doc).status_code == 201
    check = rb.owner.post(f"{rb.base}/financing-readiness", json={"run_id": rb.run["id"], "product_id": "wc-line"}).json()
    assert check["state"] == "ready"
    app = rb.owner.post(rb.url, json={"kind": "financing_application", "readiness_check_id": check["id"]}).json()
    second = rb.member("owner")
    assert _approve(rb, second, app, password=PASSWORD, verification=VERIFY | {"checked_on": rb.today.isoformat()}).status_code == 200
    app = _release(rb, rb.owner, app).json()
    r = _run(rb, rb.owner, app)
    assert r.status_code == 201, r.text
    assert r.json()["adapter"] == "application_submit" and r.json()["reference"].startswith("SBX-APP-") and r.json()["result"]["lender_decision"] is None
    assert [c[0] for c in sandbox] == ["/sandbox/payments", "/sandbox/applications"]


# --- required case 2: a denied unapproved action ----------------------------------------------------------------


def test_unapproved_or_unreleased_actions_are_blocked_and_nothing_is_sent(sandbox):
    biz = approvals_setup()
    keeper, staff = biz.member("bookkeeper"), biz.member("staff")
    pending = _reminder(biz, keeper).json()
    r = _run(biz, biz.owner, pending)
    assert r.status_code == 409
    failed = {c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]}
    assert {"released", "approved"} <= failed and "Not executed" in r.json()["detail"]["message"]

    approved = _reminder(biz, obligation_id=biz.new_invoice()).json()
    assert _approve(biz, biz.owner, approved).status_code == 200
    r = _run(biz, biz.owner, approved)
    assert r.status_code == 409 and {c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]} == {"released"}

    rejected = _reminder(biz, obligation_id=biz.new_invoice()).json()
    assert biz.owner.post(f"{biz.url}/{rejected['id']}/decision", json={"decision": "reject", "payload_sha256": rejected["payload_sha256"],
                                                                          "reason": "Customer already paid by phone"}).status_code == 200
    assert _run(biz, biz.owner, rejected).status_code == 409

    # Roles: only an owner runs actions; staff cannot even read the log.
    released = _released_reminder(biz, obligation_id=biz.new_invoice())
    assert _run(biz, keeper, released).status_code == 403 and _run(biz, staff, released).status_code == 403
    assert staff.get(f"{biz.base}/sandbox/executions").status_code == 403 and staff.get(f"{biz.base}/sandbox/adapters").status_code == 403
    assert sandbox == []                                                         # nothing reached any adapter
    log = _log(biz)
    assert [x["event"] for x in log] == ["blocked", "blocked", "blocked"] and all(x["execution_id"] is None and x["checks"] for x in log)
    assert {e["action"] for e in _events(biz) if e["outcome"] == "denied"} >= {"executor.run", "executor.read"}
    assert any(e["action"] == "executor.execute" and e["outcome"] == "conflict" and "released" in e["details"]["failed_checks"] for e in _events(biz))
    assert biz.owner.get(f"{biz.url}/{pending['id']}").json()["executed"] is False


def test_another_business_cannot_run_or_read_executions(sandbox):
    biz = approvals_setup()
    p = _released_reminder(biz)
    e = _run(biz, biz.owner, p).json()
    other = Biz()
    assert other.owner.post(f"{other.base}/sandbox/executions", json={"proposal_id": p["id"], "payload_sha256": p["payload_sha256"],
                                                                        "idempotency_key": _key(), "confirm_sandbox": True}).status_code == 404
    assert other.owner.get(f"{other.base}/sandbox/executions/{e['id']}").status_code == 404
    assert other.owner.get(f"{other.base}/sandbox/executions").json()["items"] == []
    assert _run(biz, other.owner, p).status_code == 404                          # not a member of the first business
    assert len(sandbox) == 1


# --- required case 3: a blocked fraud-held action --------------------------------------------------------------


def test_fraud_hold_after_release_blocks_execution_until_independently_cleared(sandbox):
    biz = approvals_setup()
    owner2, owner3 = biz.member("owner"), biz.member("owner")
    keeper = biz.member("bookkeeper")
    _destination(biz, keeper)
    p = _released_payment(biz, owner2)
    # After the release, the supplier's destination changes: the Fraud Monitor flags it and holds the payment.
    change = _destination(biz, keeper, account_number="9999-8888-7777", institution="City Bank", change_source="supplier_email",
                          note="New account letter by email")
    assert change["flag_id"] and change["scan"]["holds_added"] == 1
    r = _run(biz, biz.owner, p)
    assert r.status_code == 409
    failed = {c["code"]: c["detail"] for c in r.json()["detail"]["checks"] if not c["ok"]}
    assert list(failed) == ["fraud_hold"] and "Held by the Fraud Monitor" in failed["fraud_hold"]
    assert sandbox == [] and _log(biz)[0]["event"] == "blocked"
    # The database refuses an execution row for a held proposal even if the API check were skipped.
    with get_engine().connect() as conn, pytest.raises(Exception, match="on hold"):
        conn.execute(text("""INSERT INTO sandbox_execution (business_id, proposal_id, idempotency_key, adapter, payload_sha256, mock_key, request_sha256, created_by, created_role)
                             SELECT business_id, id, 'direct-insert-key-0001', 'payment_initiate', payload_sha256, repeat('a', 64), repeat('b', 64), proposed_by, 'owner'
                             FROM action_proposal WHERE id = :p"""), {"p": p["id"]})

    # An owner who proposed nothing and recorded nothing verifies the change independently; the hold lifts.
    clear = biz.owner.post(f"{biz.base}/fraud/flags/{change['flag_id']}/reviews", json={
        "decision": "reject", "reason": "Called Delta on the file number; change confirmed",
        "verification": {"method": "called_known_contact", "contact_source": "Phone number in the supplier file since 2024",
                         "contact_predates_change": True, "checked_on": biz.today.isoformat()}})
    assert clear.status_code == 409                                              # the owner proposed the held payment
    clear = owner3.post(f"{biz.base}/fraud/flags/{change['flag_id']}/reviews", json={
        "decision": "reject", "reason": "Called Delta on the file number; change confirmed",
        "verification": {"method": "called_known_contact", "contact_source": "Phone number in the supplier file since 2024",
                         "contact_predates_change": True, "checked_on": biz.today.isoformat()}})
    assert clear.status_code == 200 and clear.json()["state"] == "cleared", clear.text
    r = _run(biz, biz.owner, p)
    assert r.status_code == 201, r.text
    assert r.json()["request"]["destination"]["last4"] == "7777" and r.json()["outcome"] == "succeeded"   # the verified destination


def test_payment_without_a_destination_on_file_is_blocked(sandbox):
    biz = approvals_setup()
    p = _released_payment(biz, biz.member("owner"))
    r = _run(biz, biz.owner, p)
    assert r.status_code == 409 and [c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]] == ["destination_on_file"]
    assert sandbox == []


# --- required case 4: an idempotent retry -----------------------------------------------------------------------


def test_retry_with_the_same_key_returns_the_same_outcome_without_a_second_call(sandbox):
    biz = approvals_setup()
    p = _released_reminder(biz)
    key = _key()
    first = _run(biz, biz.owner, p, key)
    assert first.status_code == 201
    again = _run(biz, biz.owner, p, key)
    assert again.status_code == 200, again.text
    a, b = first.json(), again.json()
    assert b["replayed"] is True and b["id"] == a["id"] and b["reference"] == a["reference"] and b["outcome"] == "succeeded"
    assert b["attempts"] == 1 and b["replays"] == 1 and len(sandbox) == 1        # the adapter was called once
    assert _mock_seen(mock_key(biz.id, p["id"], p["payload_sha256"]))[0]["times_seen"] == 1
    # A new key cannot run the same proposal again, and a used key cannot run another proposal.
    other_key = _run(biz, biz.owner, p)
    assert other_key.status_code == 409 and "already sent with another idempotency key" in other_key.json()["detail"]["message"]
    q = _released_reminder(biz, obligation_id=biz.new_invoice())
    reused = _run(biz, biz.owner, q, key)
    assert reused.status_code == 409 and "already used for another proposal" in reused.json()["detail"]["message"]
    assert len(sandbox) == 1
    assert [x["event"] for x in _log(biz, proposal_id=p["id"])] == ["blocked", "replayed", "succeeded", "dispatched"]
    assert any(e["action"] == "executor.replay" for e in _events(biz))


def test_no_answer_then_retry_gives_one_outcome(sandbox, monkeypatch):
    biz = approvals_setup()
    p = _released_reminder(biz)
    key = _key()
    working = adapters.mock_client

    class Down:
        def post(self, *a, **kw):
            raise httpx.ConnectError("refused")

        def close(self):
            pass
    monkeypatch.setattr(adapters, "mock_client", lambda: Down())
    r = _run(biz, biz.owner, p, key)
    assert r.status_code == 201 and r.json()["outcome"] == "failed" and r.json()["final"] is False
    assert biz.owner.get(f"{biz.url}/{p['id']}").json()["executed"] is False
    assert _run(biz, biz.owner, p).status_code == 409                            # a different key is still refused
    monkeypatch.setattr(adapters, "mock_client", working)
    r = _run(biz, biz.owner, p, key)
    assert r.status_code == 200 and r.json()["outcome"] == "succeeded" and r.json()["attempts"] == 2
    assert [x["event"] for x in r.json()["log"]] == ["dispatched", "failed", "dispatched", "succeeded"]


def test_lost_answer_is_not_processed_twice_by_the_sandbox(sandbox):
    """The mock processed the request but the answer never arrived: the retry gets the stored result, not a second action."""
    biz = approvals_setup()
    p = _released_reminder(biz)
    mk = mock_key(biz.id, p["id"], p["payload_sha256"])
    body = {"reference": "INV-108", "customer": "Eastline Stores", "channel": "email", "amount_minor": 4_500_000, "currency": "BDT",
            "due_date": p["payload"]["due_date"], "message": MESSAGE}
    earlier = TestClient(mock_app).post("/sandbox/reminders", json=body, headers={"Idempotency-Key": mk}).json()
    r = _run(biz, biz.owner, p)
    assert r.status_code == 201 and r.json()["reference"] == earlier["reference"] and r.json()["result"]["replayed"] is True
    assert _mock_seen(mk)[0]["times_seen"] == 2                                   # seen twice, processed once


def test_sandbox_rejection_is_final(sandbox):
    biz = approvals_setup()
    big = biz.owner.post(f"{biz.base}/obligations", json={"direction": "payable", "party_name": "Delta Supplies", "document_number": "DS-900",
                                                          "issue_date": biz.today.isoformat(), "due_date": (biz.today + timedelta(days=5)).isoformat(),
                                                          "amount_minor": 60_000_000, "currency": "BDT", "category": "inventory_suppliers",
                                                          "source_reference": "book", "settlement_account_id": biz.bank}).json()["id"]
    _destination(biz, biz.owner)
    p = _released_payment(biz, biz.member("owner"), amount=60_000_000, obligation_id=big)
    key = _key()
    r = _run(biz, biz.owner, p, key)
    assert r.status_code == 201
    e = r.json()
    assert e["outcome"] == "rejected" and e["final"] and e["reference"] is None and e["result"]["code"] == "over_sandbox_limit"
    again = _run(biz, biz.owner, p, key)
    assert again.status_code == 200 and again.json()["replayed"] and again.json()["outcome"] == "rejected" and len(sandbox) == 1
    assert biz.owner.get(f"{biz.url}/{p['id']}").json()["executed"] is False


# --- expiry, tampering, validation ------------------------------------------------------------------------------


def test_expired_after_release_is_blocked(sandbox, monkeypatch):
    biz = approvals_setup()
    p = _released_reminder(biz, expires_in_hours=2)
    monkeypatch.setattr(executor_router, "utc_now", lambda: utc_now() + timedelta(hours=3))
    r = _run(biz, biz.owner, p)
    assert r.status_code == 409 and [c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]] == ["not_expired"]
    assert sandbox == []


def test_tampered_payload_or_a_different_hash_is_blocked(sandbox):
    biz = approvals_setup()
    p = _released_reminder(biz)
    other = "f" * 64
    r = _run(biz, biz.owner, p, payload_sha256=other)
    assert r.status_code == 409 and "payload_integrity" in {c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]}
    _tamper(p["id"], "UPDATE action_proposal SET payload = jsonb_set(payload, '{channel}', '\"sms\"') WHERE id = :p")
    r = _run(biz, biz.owner, p)
    assert r.status_code == 409
    assert {"payload_integrity", "business_scope"} <= {c["code"] for c in r.json()["detail"]["checks"] if not c["ok"]}
    assert sandbox == []


def test_request_validation(sandbox):
    biz = approvals_setup()
    p = _released_reminder(biz)
    for bad in ({"idempotency_key": "short"}, {"idempotency_key": "has spaces in the key 123"}, {"idempotency_key": "x" * 101},
                {"confirm_sandbox": False}, {"confirm_sandbox": "yes"}, {"payload_sha256": "abc"}, {"extra": 1}, {"proposal_id": "not-a-uuid"}):
        assert _run(biz, biz.owner, p, **bad).status_code == 422, bad
    assert biz.owner.post(f"{biz.base}/sandbox/executions", json={"proposal_id": p["id"]}).status_code == 422
    assert _run(biz, biz.owner, p | {"id": str(uuid.uuid4())}).status_code == 404
    assert sandbox == []


# --- adapters and the mock boundary -----------------------------------------------------------------------------


def test_adapters_are_separate_and_packet_sharing_is_ready_but_unused(sandbox):
    biz = approvals_setup()
    a = biz.owner.get(f"{biz.base}/sandbox/adapters").json()
    assert a["mode"] == "sandbox" and a["credentials"] == "none"
    by = {x["code"]: x for x in a["adapters"]}
    assert set(by) == {"reminder_send", "application_submit", "payment_initiate", "packet_share"}
    assert len({x["endpoint"] for x in by.values()}) == 4 and by["packet_share"]["available"] is False and "Prompt 20" in by["packet_share"]["note"]
    assert "funding_packet_share" not in adapters.BY_KIND
    # The packet adapter itself works against its mock endpoint.
    packet = adapters.ADAPTERS["packet_share"]
    req = packet.build({"packet_id": "pkt-1", "recipient": "Sandbox lender", "fields": ["cash_summary", "obligations"], "expires_at": "2026-12-31T00:00:00+00:00"}, {})
    out = packet.send(req, "c" * 64)
    assert out.event == "succeeded" and out.reference.startswith("SBX-PKT-") and out.result["fields_received"] == 2
    assert sandbox[-1] == ("/sandbox/packets", "c" * 64)


def test_mock_service_enforces_its_contract():
    mock = TestClient(mock_app)
    body = {"reference": "INV-1", "customer": "A", "channel": "email", "amount_minor": 100, "currency": "BDT", "due_date": "2026-10-01",
            "message": "Please pay invoice INV-1."}
    key = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    assert mock.post("/sandbox/reminders", json=body).status_code == 422                                   # no idempotency key
    assert mock.post("/sandbox/reminders", json=body, headers={"Idempotency-Key": "abc"}).status_code == 422
    assert mock.post("/sandbox/reminders", json=body | {"token": "x"}, headers={"Idempotency-Key": key}).status_code == 422
    first = mock.post("/sandbox/reminders", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 200 and first.json()["replayed"] is False and first.json()["delivered"] is False
    assert mock.post("/sandbox/reminders", json=body, headers={"Idempotency-Key": key}).json()["replayed"] is True
    assert mock.post("/sandbox/reminders", json=body | {"amount_minor": 200}, headers={"Idempotency-Key": key}).status_code == 422
    assert mock.post("/sandbox/payments", json={"reference": "B", "payee": "P", "destination": {"kind": "bank", "institution": "XB", "last4": "1234"},
                                                "from_account": "Main", "amount_minor": 100, "currency": "USD"},
                     headers={"Idempotency-Key": hashlib.sha256(b"usd" + uuid.uuid4().bytes).hexdigest()}).json()["code"] == "currency_not_supported"


def test_logs_hold_no_account_numbers_or_message_text(sandbox):
    biz = approvals_setup()
    _destination(biz, biz.owner)
    pay = _released_payment(biz, biz.member("owner"))
    assert _run(biz, biz.owner, pay).status_code == 201
    rem = _released_reminder(biz)
    assert _run(biz, biz.owner, rem).status_code == 201
    with get_engine().connect() as conn:
        stored = " ".join(str(r) for r in conn.execute(text("SELECT request, result, checks FROM sandbox_execution_log WHERE business_id = :b"), {"b": biz.id}))
    assert "123456789012" not in stored.replace(" ", "") and "Please arrange payment" not in stored and "9012" in stored
    assert not re.search(r"\d{8}", re.sub(r"[0-9a-f]{64}|\d{4}-\d{2}-\d{2}T[\d:.+]+|\d{4}-\d{2}-\d{2}", "", stored).replace(" ", ""))


# --- database guards ----------------------------------------------------------------------------------------------


def test_database_guards(sandbox):
    biz = approvals_setup()
    p = _released_reminder(biz)
    e = _run(biz, biz.owner, p).json()
    pending = _reminder(biz, obligation_id=biz.new_invoice()).json()
    with get_engine().connect() as conn:
        for sql in ("UPDATE sandbox_execution SET adapter = 'packet_share' WHERE id = :e", "DELETE FROM sandbox_execution WHERE id = :e",
                    "UPDATE sandbox_execution_log SET reference = NULL WHERE execution_id = :e", "DELETE FROM sandbox_execution_log WHERE execution_id = :e"):
            with pytest.raises(Exception, match="append-only"):
                conn.execute(text(sql), {"e": e["id"]})
            conn.rollback()
        with pytest.raises(Exception, match="approved and released"):
            conn.execute(text("""INSERT INTO sandbox_execution (business_id, proposal_id, idempotency_key, adapter, payload_sha256, mock_key, request_sha256, created_by, created_role)
                                 SELECT business_id, id, 'direct-insert-key-0002', 'reminder_send', payload_sha256, repeat('d', 64), repeat('e', 64), proposed_by, 'owner'
                                 FROM action_proposal WHERE id = :p"""), {"p": pending["id"]})
        conn.rollback()
        with pytest.raises(Exception, match="final outcome"):
            conn.execute(text("""INSERT INTO sandbox_execution_log (business_id, proposal_id, execution_id, event, adapter, idempotency_key, actor_id, actor_role, request)
                                 SELECT business_id, proposal_id, id, 'dispatched', adapter, idempotency_key, created_by, 'owner', '{}'::jsonb
                                 FROM sandbox_execution WHERE id = :e"""), {"e": e["id"]})
        conn.rollback()
        with pytest.raises(Exception, match="unique|duplicate"):
            conn.execute(text("""INSERT INTO sandbox_execution_log (business_id, proposal_id, execution_id, event, adapter, idempotency_key, actor_id, actor_role, result, reference)
                                 SELECT business_id, proposal_id, id, 'succeeded', adapter, idempotency_key, created_by, 'owner', '{}'::jsonb, 'SBX-RMD-0000000000'
                                 FROM sandbox_execution WHERE id = :e"""), {"e": e["id"]})
        conn.rollback()

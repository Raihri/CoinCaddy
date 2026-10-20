"""Audit Trail tests against the real development database (migrate first)."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db import get_engine
from app.fixtures.seed import seed
from app.main import app
from app.modules.audit.redaction import REDACTED, redact
from app.modules.audit.service import verify_chain
from app.modules.identity import router as identity_router

PASSWORD = get_settings().demo_user_password
OWNER, BOOKKEEPER, STAFF, OTHER_OWNER = (
    "amina@northstar.example",
    "bilal@northstar.example",
    "chaya@northstar.example",
    "tanvir@riverbend.example",
)


@pytest.fixture(autouse=True)
def ids():
    return seed()


@pytest.fixture
def northstar(ids):
    return ids["northstar-trading"]


@pytest.fixture
def riverbend(ids):
    return ids["riverbend-crafts"]


def signed_in(email: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


def last_seq() -> int:
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT coalesce(max(seq), 0) FROM audit_event")).scalar_one()


def events_since(seq: int) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(
            text("SELECT * FROM audit_event WHERE seq > :s ORDER BY seq"), {"s": seq}
        ).mappings()]


def membership_id(business_id: str, email: str) -> str:
    with get_engine().connect() as conn:
        return str(conn.execute(
            text("SELECT m.id FROM membership m JOIN app_user u ON u.id = m.user_id "
                 "WHERE m.business_id = :b AND u.email = :e"),
            {"b": business_id, "e": email},
        ).scalar_one())


def role_of(business_id: str, email: str) -> str:
    with get_engine().connect() as conn:
        return conn.execute(
            text("SELECT m.role FROM membership m JOIN app_user u ON u.id = m.user_id "
                 "WHERE m.business_id = :b AND u.email = :e"),
            {"b": business_id, "e": email},
        ).scalar_one()


# --- Redaction -------------------------------------------------------------------------------------


def test_redaction_masks_account_numbers_and_drops_document_content():
    out = redact({
        "password": "hunter2",
        "session_token": "abc",
        "document_content": "Invoice text with bank details",
        "file_name": "statement.csv",
        "raw_row": ["1234567890123", "x"],
        "account_number": "0123-4567-8901",
        "memo": "Paid to A/C 1234 5678 9012 3456 today",
        "phone_like": "01712345678",
        "short_number": "INV-108",
        "amount_minor": 6500000,
        "nested": {"iban": "BD12 3456 7890 1234 5678 90"},
    })
    assert out["password"] == out["session_token"] == out["document_content"] == REDACTED
    assert out["file_name"] == out["raw_row"] == REDACTED
    assert out["account_number"] == "••••8901"
    assert out["memo"] == "Paid to A/C ••••3456 today"
    assert out["phone_like"] == "••••5678"
    assert out["short_number"] == "INV-108"
    assert out["amount_minor"] == 6500000
    assert out["nested"]["iban"] == "••••7890"


def test_redaction_keeps_record_ids_and_dates_and_truncates_long_text():
    uuid_str = "12345678-1234-1234-1234-123456789012"
    out = redact({"requested_business_id": uuid_str, "date": "2026-10-20", "note": "x" * 500})
    assert out["requested_business_id"] == uuid_str
    assert out["date"] == "2026-10-20"
    assert len(out["note"]) < 250 and out["note"].endswith("[truncated]")


def test_redaction_refuses_float_money():
    with pytest.raises(TypeError):
        redact({"amount": 65000.0})


# --- Events recorded for sensitive operations --------------------------------------------------------


def test_sign_in_success_and_failure_are_recorded_without_secrets():
    start = last_seq()
    client = signed_in(OWNER)
    TestClient(app).post("/api/auth/login", json={"email": OWNER, "password": "wrong-password"})
    TestClient(app).post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    client.post("/api/auth/logout")
    ev = [(e["action"], e["outcome"]) for e in events_since(start)]
    assert ev == [("auth.sign_in", "success"), ("auth.sign_in", "failed"),
                  ("auth.sign_in", "failed"), ("auth.sign_out", "success")]
    stored = json.dumps([e["details"] for e in events_since(start)])
    assert "wrong-password" not in stored and PASSWORD not in stored
    assert "a***@northstar.example" in stored and "n***@example.com" in stored
    assert all(e["business_id"] is None for e in events_since(start))  # user-level, not on business pages


def test_quick_demo_sign_in_requires_opt_in_and_only_accepts_synthetic_users(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "demo_quick_login_enabled", False)
    client = TestClient(app)
    assert client.post("/api/auth/quick-login", json={"email": OWNER}).status_code == 404

    monkeypatch.setattr(settings, "demo_quick_login_enabled", True)
    response = client.post("/api/auth/quick-login", json={"email": OWNER})
    assert response.status_code == 200
    assert response.json()["user"]["email"] == OWNER
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/quick-login", json={"email": "not-a-demo@example.com"}).status_code == 404


def test_role_change_is_recorded_with_actor_business_and_outcome(northstar):
    start = last_seq()
    target = membership_id(northstar, STAFF)
    assert signed_in(OWNER).patch(
        f"/api/businesses/{northstar}/memberships/{target}", json={"role": "bookkeeper"}
    ).status_code == 200
    [e] = [e for e in events_since(start) if e["action"] == "membership.role_change"]
    assert e["outcome"] == "success"
    assert str(e["business_id"]) == northstar
    assert (e["actor_name"], e["actor_role"]) == ("Amina Rahman", "owner")
    assert (e["target_type"], e["target_id"]) == ("membership", target)
    assert e["details"] == {"member_name": "Chaya Das", "from_role": "staff", "to_role": "bookkeeper"}
    assert e["occurred_at"].tzinfo is not None


def test_denied_and_conflicting_attempts_are_recorded(northstar):
    start = last_seq()
    owner_m = membership_id(northstar, OWNER)
    signed_in(STAFF).patch(f"/api/businesses/{northstar}/memberships/{owner_m}", json={"role": "staff"})
    signed_in(OWNER).patch(f"/api/businesses/{northstar}/memberships/{owner_m}", json={"role": "staff"})
    ev = [(e["action"], e["outcome"], e["actor_role"]) for e in events_since(start) if e["business_id"]]
    assert ("membership.manage", "denied", "staff") in ev
    assert ("membership.role_change", "conflict", "owner") in ev


def test_export_event_records_counts_not_content(northstar):
    start = last_seq()
    assert signed_in(BOOKKEEPER).get(f"/api/businesses/{northstar}/export").status_code == 200
    [e] = [e for e in events_since(start) if e["action"] == "business.export"]
    assert e["outcome"] == "success" and e["actor_role"] == "bookkeeper"
    assert e["details"]["account_count"] == 3 and e["details"]["member_count"] == 3
    stored = json.dumps(e["details"])
    assert "Operating bank account" not in stored and "ACC-BANK" not in stored


def test_audit_write_failure_rolls_back_the_role_change(northstar, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    target = membership_id(northstar, STAFF)
    client = signed_in(OWNER)
    monkeypatch.setattr(identity_router, "record_event", broken)
    client_no_raise = TestClient(app, raise_server_exceptions=False, cookies=client.cookies)
    r = client_no_raise.patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "bookkeeper"})
    assert r.status_code == 500
    assert role_of(northstar, STAFF) == "staff"


# --- Read access is business-scoped -----------------------------------------------------------------


def test_one_real_event_from_the_demo_flow_appears_on_the_audit_page(northstar):
    owner = signed_in(OWNER)
    target = membership_id(northstar, STAFF)
    owner.patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "bookkeeper"})
    events = owner.get(f"/api/businesses/{northstar}/audit-events?limit=5").json()["events"]
    newest = events[0]
    assert newest["action"] == "membership.role_change" and newest["outcome"] == "success"
    assert newest["details"] == {"member_name": "Chaya Das", "from_role": "staff", "to_role": "bookkeeper"}
    assert newest["actor_name"] == "Amina Rahman"


def test_user_cannot_read_another_businesss_events(northstar, riverbend):
    start = last_seq()
    amina = signed_in(OWNER)
    r = amina.get(f"/api/businesses/{riverbend}/audit-events")
    assert r.status_code == 404 and "Riverbend" not in r.text
    # Amina's probe is logged as a user-level event, not written into Riverbend's history.
    probe = [e for e in events_since(start) if e["action"] == "audit.read"]
    assert probe and all(e["business_id"] is None for e in probe)
    riverbend_owner_view = signed_in(OTHER_OWNER).get(f"/api/businesses/{riverbend}/audit-events?limit=100").json()["events"]
    assert all(e["actor_name"] != "Amina Rahman" for e in riverbend_owner_view)
    # And Northstar's history never contains Riverbend rows.
    with get_engine().connect() as conn:
        ns_ids = {str(r[0]) for r in conn.execute(text("SELECT id FROM audit_event WHERE business_id = :b"), {"b": northstar})}
        rb_ids = {str(r[0]) for r in conn.execute(text("SELECT id FROM audit_event WHERE business_id = :b"), {"b": riverbend})}
    page = {e["id"] for e in amina.get(f"/api/businesses/{northstar}/audit-events?limit=100").json()["events"]}
    assert page <= ns_ids and not (page & rb_ids)


def test_staff_cannot_read_audit_but_bookkeeper_can(northstar):
    assert signed_in(STAFF).get(f"/api/businesses/{northstar}/audit-events").status_code == 403
    assert signed_in(BOOKKEEPER).get(f"/api/businesses/{northstar}/audit-events").status_code == 200


def test_pagination_and_limit_validation(northstar):
    owner = signed_in(OWNER)
    base = f"/api/businesses/{northstar}/audit-events"
    assert owner.get(f"{base}?limit=0").status_code == 422
    assert owner.get(f"{base}?limit=101").status_code == 422
    assert owner.get(f"{base}?before_seq=0").status_code == 422
    first = owner.get(f"{base}?limit=2").json()
    assert len(first["events"]) == 2 and first["next_before_seq"] == first["events"][-1]["seq"]
    second = owner.get(f"{base}?limit=2&before_seq={first['next_before_seq']}").json()
    assert all(e["seq"] < first["events"][-1]["seq"] for e in second["events"])


# --- Append-only ------------------------------------------------------------------------------------


def test_user_cannot_edit_or_delete_events_through_the_api(northstar):
    owner = signed_in(OWNER)
    event = owner.get(f"/api/businesses/{northstar}/audit-events?limit=1").json()["events"][0]
    base = f"/api/businesses/{northstar}/audit-events"
    for method in ("put", "patch", "delete"):
        assert getattr(owner, method)(f"{base}/{event['id']}").status_code in (404, 405)
        assert getattr(owner, method)(base).status_code == 405
    assert owner.post(base, json={"action": "fake.event"}).status_code == 405
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT event_hash FROM audit_event WHERE id = :i"), {"i": event["id"]}).scalar_one()
    assert row == event["event_hash"]


@pytest.mark.parametrize("statement", [
    "UPDATE audit_event SET outcome = 'success' WHERE seq = (SELECT max(seq) FROM audit_event)",
    "DELETE FROM audit_event WHERE seq = (SELECT max(seq) FROM audit_event)",
    "TRUNCATE audit_event",
])
def test_database_rejects_update_delete_truncate(statement):
    before = last_seq()
    with pytest.raises(DBAPIError, match="append-only"):
        with get_engine().begin() as conn:
            conn.execute(text(statement))
    assert last_seq() == before


def test_concurrent_appends_keep_a_single_unbroken_chain():
    from concurrent.futures import ThreadPoolExecutor

    start = last_seq()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: TestClient(app).post("/api/auth/login", json={"email": OWNER, "password": "bad"}), range(16)))
    new = events_since(start)
    assert len(new) >= 16
    assert len({e["prev_hash"] for e in new}) == len(new)  # no two events claim the same predecessor
    with get_engine().connect() as conn:
        assert verify_chain(conn)[0]


def test_hash_chain_verifies_and_detects_out_of_band_tampering():
    signed_in(OWNER)  # make sure there is at least one event
    with get_engine().connect() as conn:
        ok, count, bad = verify_chain(conn)
        assert ok and count > 0 and bad is None
        conn.rollback()
        # Simulate a privileged out-of-band edit inside a transaction that is rolled back.
        trans = conn.begin()
        conn.execute(text("ALTER TABLE audit_event DISABLE TRIGGER audit_event_no_update_delete"))
        conn.execute(text(
            "UPDATE audit_event SET outcome = CASE outcome WHEN 'success' THEN 'denied' ELSE 'success' END "
            "WHERE seq = (SELECT max(seq) FROM audit_event)"
        ))
        ok, _, bad = verify_chain(conn)
        trans.rollback()
    assert not ok and bad is not None
    with get_engine().connect() as conn:
        assert verify_chain(conn)[0]

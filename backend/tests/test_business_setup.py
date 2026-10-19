"""Business Setup tests (separate test database; see conftest.py)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.contracts.reference import MAX_AMOUNT_MINOR
from app.db import get_engine
from app.fixtures.seed import seed
from app.main import app
from app.modules.identity.passwords import hash_password

PASSWORD = get_settings().demo_user_password
OWNER, BOOKKEEPER, STAFF = "amina@northstar.example", "bilal@northstar.example", "chaya@northstar.example"

VALID = {
    "name": "Test Traders",
    "jurisdiction": "BD",
    "base_currency": "BDT",
    "timezone": "Asia/Dhaka",
    "reserve": {"amount_minor": 5_000_000, "currency": "BDT"},
    "accounts": [
        {"label": "Main bank", "kind": "bank", "currency": "BDT"},
        {"label": "Wallet", "kind": "mobile_wallet", "currency": "BDT"},
    ],
}


@pytest.fixture(autouse=True)
def ids():
    return seed()


def signed_in(email: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def fresh_user() -> TestClient:
    """A brand-new user with no memberships, so tests never pile businesses onto demo users."""
    email = f"setup-{uuid.uuid4().hex[:10]}@test.example"
    with get_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO app_user (email, display_name, password_hash) VALUES (:e, 'Test Owner', :h)"),
            {"e": email, "h": hash_password(PASSWORD)},
        )
    return signed_in(email)


@pytest.fixture
def owned(fresh_user):
    r = fresh_user.post("/api/businesses", json=VALID)
    assert r.status_code == 201, r.text
    return fresh_user, r.json()


def audit_rows(business_id: str, action: str) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(
            text("SELECT * FROM audit_event WHERE business_id = :b AND action = :a ORDER BY seq"),
            {"b": business_id, "a": action},
        ).mappings()]


def business_row(business_id: str) -> dict:
    with get_engine().connect() as conn:
        return dict(conn.execute(text("SELECT * FROM business WHERE id = :b"), {"b": business_id}).mappings().one())


def with_(**changes):
    body = {**VALID, **changes}
    return body


# --- Create -----------------------------------------------------------------------------------------


def test_create_business_makes_creator_owner_and_stores_minor_units(owned):
    client, biz = owned
    assert biz["your_role"] == "owner" and biz["version"] == 1 and biz["is_synthetic"] is False
    assert biz["reserve"] == {"amount_minor": 5_000_000, "currency": "BDT"}
    assert [a["label"] for a in biz["accounts"]] == ["Main bank", "Wallet"]
    assert biz["jurisdiction_name"] == "Bangladesh"
    row = business_row(biz["id"])
    assert type(row["reserve_amount_minor"]) is int and row["reserve_amount_minor"] == 5_000_000
    me = client.get("/api/auth/me").json()
    assert [(m["business_id"], m["role"]) for m in me["memberships"]] == [(biz["id"], "owner")]
    [event] = audit_rows(biz["id"], "business.create")
    assert event["outcome"] == "success" and event["details"]["reserve_amount_minor"] == 5_000_000


def test_lowercase_codes_are_normalized(fresh_user):
    r = fresh_user.post("/api/businesses", json=with_(jurisdiction="bd", base_currency="bdt",
                                                      reserve={"amount_minor": 1, "currency": "bdt"}, accounts=[]))
    assert r.status_code == 201 and r.json()["base_currency"] == "BDT" and r.json()["jurisdiction"] == "BD"


def test_text_is_trimmed_and_whitespace_collapsed(fresh_user):
    r = fresh_user.post("/api/businesses", json=with_(name="   Rupa    Stores  ", accounts=[]))
    assert r.json()["name"] == "Rupa Stores"


@pytest.mark.parametrize("body, field", [
    (with_(name=""), "name"),
    (with_(name="    "), "name"),
    (with_(name="x" * 121), "name"),
    (with_(name="Bad\u0000Name"), "name"),
    (with_(name="Hidden​space"), "name"),
    (with_(name=123), "name"),
    (with_(jurisdiction="XX"), "jurisdiction"),
    (with_(jurisdiction=""), "jurisdiction"),
    (with_(base_currency="XYZ"), "base_currency"),
    (with_(timezone="Mars/Olympus"), "timezone"),
    (with_(timezone="UTC+6"), "timezone"),
    (with_(timezone="asia/dhaka"), "timezone"),
    (with_(timezone="../../etc/passwd"), "timezone"),
    (with_(reserve={"amount_minor": 100.5, "currency": "BDT"}), "amount_minor"),
    (with_(reserve={"amount_minor": "1000", "currency": "BDT"}), "amount_minor"),
    (with_(reserve={"amount_minor": True, "currency": "BDT"}), "amount_minor"),
    (with_(reserve={"amount_minor": -1, "currency": "BDT"}), "amount_minor"),
    (with_(reserve={"amount_minor": MAX_AMOUNT_MINOR + 1, "currency": "BDT"}), "amount_minor"),
    (with_(reserve={"amount_minor": 100, "currency": "USD"}), "reserve currency must equal"),
    (with_(reserve={"amount_minor": 100}), "currency"),
    (with_(reserve={"amount_minor": 100, "currency": "BDT", "extra": 1}), "extra"),
    (with_(unexpected="x"), "unexpected"),
    (with_(accounts=[{"label": "Bank", "kind": "bank", "currency": "BDT"},
                     {"label": "BANK", "kind": "cash", "currency": "BDT"}]), "unique"),
    (with_(accounts=[{"label": "", "kind": "bank", "currency": "BDT"}]), "label"),
    (with_(accounts=[{"label": "x" * 61, "kind": "bank", "currency": "BDT"}]), "label"),
    (with_(accounts=[{"label": "Crypto", "kind": "crypto", "currency": "BDT"}]), "kind"),
    (with_(accounts=[{"label": "Bank", "kind": "bank", "currency": "BTC"}]), "currency"),
    (with_(accounts=[{"label": f"A{i}", "kind": "cash", "currency": "BDT"} for i in range(21)]), "accounts"),
])
def test_invalid_create_inputs_are_rejected(fresh_user, body, field):
    r = fresh_user.post("/api/businesses", json=body)
    assert r.status_code == 422, r.text
    assert field in r.text
    assert fresh_user.get("/api/auth/me").json()["memberships"] == []  # nothing was created


def test_reserve_at_the_maximum_is_stored_exactly(fresh_user):
    r = fresh_user.post("/api/businesses", json=with_(reserve={"amount_minor": MAX_AMOUNT_MINOR, "currency": "BDT"}, accounts=[]))
    assert r.status_code == 201 and r.json()["reserve"]["amount_minor"] == 10**15


def test_membership_cap_stops_unbounded_business_creation(fresh_user):
    for _ in range(10):
        assert fresh_user.post("/api/businesses", json=with_(accounts=[])).status_code == 201
    assert fresh_user.post("/api/businesses", json=with_(accounts=[])).status_code == 409


def test_create_requires_sign_in():
    assert TestClient(app).post("/api/businesses", json=VALID).status_code == 401


# --- Edit settings ------------------------------------------------------------------------------------


def test_owner_edit_persists_after_reload_and_is_audited(owned):
    client, biz = owned
    r = client.patch(f"/api/businesses/{biz['id']}/settings", json={
        "expected_version": 1, "name": "Test Traders Ltd", "timezone": "Asia/Kolkata",
        "reserve": {"amount_minor": 7_550_025, "currency": "BDT"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    # "Reload": a brand-new client session reading from the database.
    reloaded = TestClient(app, cookies=client.cookies).get(f"/api/businesses/{biz['id']}").json()
    assert (reloaded["name"], reloaded["timezone"], reloaded["reserve"]["amount_minor"], reloaded["version"]) == (
        "Test Traders Ltd", "Asia/Kolkata", 7_550_025, 2)
    [event] = [e for e in audit_rows(biz["id"], "business.settings_update") if e["outcome"] == "success"]
    assert event["details"]["changes"] == {
        "name": {"from": "Test Traders", "to": "Test Traders Ltd"},
        "timezone": {"from": "Asia/Dhaka", "to": "Asia/Kolkata"},
        "reserve_amount_minor": {"from": 5_000_000, "to": 7_550_025},
    }
    assert event["actor_role"] == "owner" and event["details"]["version"] == 2


def test_changing_base_currency_requires_restating_the_reserve(owned):
    client, biz = owned
    url = f"/api/businesses/{biz['id']}/settings"
    r = client.patch(url, json={"expected_version": 1, "base_currency": "USD"})
    assert r.status_code == 422 and "restating the reserve" in r.text
    r = client.patch(url, json={"expected_version": 1, "base_currency": "USD", "reserve": {"amount_minor": 100, "currency": "BDT"}})
    assert r.status_code == 422 and "reserve currency must equal" in r.text
    r = client.patch(url, json={"expected_version": 1, "jurisdiction": "US", "base_currency": "USD",
                                "reserve": {"amount_minor": 250_000, "currency": "USD"}})
    assert r.status_code == 200 and r.json()["reserve"] == {"amount_minor": 250_000, "currency": "USD"}
    assert business_row(biz["id"])["base_currency"] == "USD"


@pytest.mark.parametrize("body, needle", [
    ({"expected_version": 1}, "at least one"),
    ({"name": "No version"}, "expected_version"),
    ({"expected_version": 0, "name": "x"}, "expected_version"),
    ({"expected_version": "1", "name": "x"}, "expected_version"),
    ({"expected_version": 1, "name": None}, "null"),
    ({"expected_version": 1, "timezone": "Not/AZone"}, "timezone"),
    ({"expected_version": 1, "reserve": {"amount_minor": 12.5, "currency": "BDT"}}, "amount_minor"),
    ({"expected_version": 1, "reserve": {"amount_minor": -5, "currency": "BDT"}}, "amount_minor"),
    ({"expected_version": 1, "jurisdiction": "ZZ"}, "jurisdiction"),
    ({"expected_version": 1, "slug": "hijack"}, "slug"),
    ({"expected_version": 1, "is_synthetic": True}, "is_synthetic"),
])
def test_invalid_settings_edits_are_rejected_and_nothing_changes(owned, body, needle):
    client, biz = owned
    before = business_row(biz["id"])
    r = client.patch(f"/api/businesses/{biz['id']}/settings", json=body)
    assert r.status_code == 422, r.text
    assert needle in r.text
    assert business_row(biz["id"]) == before


def test_stale_version_is_refused(owned):
    client, biz = owned
    url = f"/api/businesses/{biz['id']}/settings"
    assert client.patch(url, json={"expected_version": 1, "name": "First edit"}).status_code == 200
    r = client.patch(url, json={"expected_version": 1, "name": "Second edit from an old screen"})
    assert r.status_code == 409 and "version 2" in r.text
    assert business_row(biz["id"])["name"] == "First edit"
    assert any(e["outcome"] == "conflict" for e in audit_rows(biz["id"], "business.settings_update"))


def test_no_op_edit_does_not_bump_version_or_audit(owned):
    client, biz = owned
    r = client.patch(f"/api/businesses/{biz['id']}/settings", json={"expected_version": 1, "name": "Test Traders"})
    assert r.status_code == 200 and r.json()["version"] == 1
    assert audit_rows(biz["id"], "business.settings_update") == []


# --- Accounts -----------------------------------------------------------------------------------------


def test_owner_adds_and_relabels_accounts_with_audit(owned):
    client, biz = owned
    base = f"/api/businesses/{biz['id']}/accounts"
    r = client.post(base, json={"label": "Petty cash", "kind": "cash", "currency": "BDT"})
    assert r.status_code == 201
    acc = next(a for a in r.json()["accounts"] if a["label"] == "Petty cash")
    r = client.patch(f"{base}/{acc['id']}", json={"label": "Shop cash box"})
    assert r.status_code == 200
    reloaded = TestClient(app, cookies=client.cookies).get(f"/api/businesses/{biz['id']}").json()
    assert "Shop cash box" in [a["label"] for a in reloaded["accounts"]]
    assert audit_rows(biz["id"], "account.create")[0]["details"]["label"] == "Petty cash"
    assert audit_rows(biz["id"], "account.relabel")[0]["details"] == {"from": "Petty cash", "to": "Shop cash box"}


def test_duplicate_account_label_is_refused_ignoring_case(owned):
    client, biz = owned
    base = f"/api/businesses/{biz['id']}/accounts"
    assert client.post(base, json={"label": "main BANK", "kind": "bank", "currency": "BDT"}).status_code == 409
    wallet = next(a for a in biz["accounts"] if a["label"] == "Wallet")
    assert client.patch(f"{base}/{wallet['id']}", json={"label": "MAIN bank"}).status_code == 409
    assert [a["label"] for a in client.get(f"/api/businesses/{biz['id']}").json()["accounts"]] == ["Main bank", "Wallet"]


def test_account_limit_is_enforced(owned):
    client, biz = owned
    base = f"/api/businesses/{biz['id']}/accounts"
    for i in range(18):
        assert client.post(base, json={"label": f"Extra {i}", "kind": "cash", "currency": "BDT"}).status_code == 201
    assert client.post(base, json={"label": "One too many", "kind": "cash", "currency": "BDT"}).status_code == 409


@pytest.mark.parametrize("body", [
    {"label": "  "}, {"label": "x" * 61}, {"label": 5}, {"label": "ok", "currency": "USD"}, {},
])
def test_invalid_relabel_is_rejected(owned, body):
    client, biz = owned
    acc = biz["accounts"][0]
    assert client.patch(f"/api/businesses/{biz['id']}/accounts/{acc['id']}", json=body).status_code == 422


# --- Permissions and isolation --------------------------------------------------------------------


@pytest.mark.parametrize("email", [STAFF, BOOKKEEPER])
def test_staff_and_bookkeeper_cannot_edit_settings(ids, email):
    northstar = ids["northstar-trading"]
    before = business_row(northstar)
    client = signed_in(email)
    acc_id = client.get(f"/api/businesses/{northstar}").json()["accounts"][0]["id"]
    assert client.patch(f"/api/businesses/{northstar}/settings",
                        json={"expected_version": before["version"], "name": "Hijacked"}).status_code == 403
    assert client.post(f"/api/businesses/{northstar}/accounts",
                       json={"label": "Sneaky", "kind": "cash", "currency": "BDT"}).status_code == 403
    assert client.patch(f"/api/businesses/{northstar}/accounts/{acc_id}", json={"label": "Renamed"}).status_code == 403
    assert business_row(northstar) == before
    denied = [e for e in audit_rows(northstar, "business.settings.update") if e["outcome"] == "denied"]
    assert denied and denied[-1]["actor_role"] in ("staff", "bookkeeper")


def test_staff_can_read_saved_settings(ids):
    body = signed_in(STAFF).get(f"/api/businesses/{ids['northstar-trading']}").json()
    assert body["base_currency"] == "BDT" and body["timezone"] == "Asia/Dhaka" and body["is_synthetic"] is True


def test_owner_cannot_edit_another_business_or_its_accounts(ids):
    northstar, riverbend = ids["northstar-trading"], ids["riverbend-crafts"]
    with get_engine().connect() as conn:
        rb_account = str(conn.execute(text("SELECT id FROM cash_account WHERE business_id = :b"), {"b": riverbend}).scalar_one())
    before = business_row(riverbend)
    amina = signed_in(OWNER)
    assert amina.patch(f"/api/businesses/{riverbend}/settings", json={"expected_version": 1, "name": "x"}).status_code == 404
    assert amina.patch(f"/api/businesses/{riverbend}/accounts/{rb_account}", json={"label": "x"}).status_code == 404
    # Own business in the path, foreign account id: still not found.
    assert amina.patch(f"/api/businesses/{northstar}/accounts/{rb_account}", json={"label": "x"}).status_code == 404
    assert business_row(riverbend) == before


def test_settings_options_require_sign_in_and_list_minor_units():
    assert TestClient(app).get("/api/reference/settings-options").status_code == 401
    opts = signed_in(STAFF).get("/api/reference/settings-options").json()
    exps = {c["code"]: c["minor_unit_exponent"] for c in opts["currencies"]}
    assert exps["BDT"] == 2 and exps["JPY"] == 0
    assert "Asia/Dhaka" in opts["timezones"] and {"code": "BD", "name": "Bangladesh",
                                                   "suggested_currency": "BDT", "suggested_timezone": "Asia/Dhaka"} in opts["jurisdictions"]


def test_database_constraints_back_up_the_api(owned):
    _, biz = owned
    from sqlalchemy.exc import IntegrityError
    for stmt in (
        "UPDATE business SET reserve_currency = 'USD' WHERE id = :b",
        "UPDATE business SET reserve_amount_minor = -1 WHERE id = :b",
        "UPDATE business SET name = '' WHERE id = :b",
    ):
        with pytest.raises(IntegrityError):
            with get_engine().begin() as conn:
                conn.execute(text(stmt), {"b": biz["id"]})

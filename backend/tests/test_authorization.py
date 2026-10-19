"""Business Access authorization tests against the real development database (migrate first).

Each test re-runs the idempotent seed, which restores the demo users' roles.
"""

import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.fixtures.seed import seed
from app.main import app
from app.modules.identity.deps import current_user
from app.modules.identity.policy import ROLE_MATRIX, ROLES, is_allowed

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
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return client


def membership_id(business_id: str, email: str) -> str:
    with get_engine().connect() as conn:
        return str(
            conn.execute(
                text(
                    "SELECT m.id FROM membership m JOIN app_user u ON u.id = m.user_id "
                    "WHERE m.business_id = :b AND u.email = :e"
                ),
                {"b": business_id, "e": email},
            ).scalar_one()
        )


def role_of(business_id: str, email: str) -> str:
    with get_engine().connect() as conn:
        return conn.execute(
            text(
                "SELECT m.role FROM membership m JOIN app_user u ON u.id = m.user_id "
                "WHERE m.business_id = :b AND u.email = :e"
            ),
            {"b": business_id, "e": email},
        ).scalar_one()


# --- Policy: deny by default -------------------------------------------------------------------


def test_unknown_action_is_denied_for_every_role():
    for role in ROLES:
        assert not is_allowed(role, "payments.execute")
        assert not is_allowed(role, "")
    assert not is_allowed(None, "business.read")
    assert not is_allowed("admin", "business.read")


def test_role_matrix_only_names_known_roles_and_reserves_owner_settings():
    for roles in ROLE_MATRIX.values():
        assert roles <= set(ROLES)
    assert ROLE_MATRIX["membership.manage"] == {"owner"}
    assert "staff" not in ROLE_MATRIX["business.export"]


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def test_every_business_route_declares_a_known_action():
    """No route under /api/businesses/{business_id} may skip the role check."""
    checked = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/businesses/{business_id}"):
            continue
        actions = [getattr(c, "required_action", None) for c in _dependency_calls(route.dependant)]
        actions = [a for a in actions if a]
        assert len(actions) == 1 and actions[0] in ROLE_MATRIX, route.path
        checked += 1
    assert checked >= 4


def test_every_non_public_api_route_requires_sign_in():
    public = {"/api/health/live", "/api/health/ready", "/api/auth/login"}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api") and route.path not in public:
            assert current_user in set(_dependency_calls(route.dependant)), route.path


# --- Sign-in -----------------------------------------------------------------------------------


def test_wrong_password_and_unknown_email_get_the_same_answer():
    client = TestClient(app)
    a = client.post("/api/auth/login", json={"email": OWNER, "password": "wrong"})
    b = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_requests_without_a_session_are_rejected(northstar):
    client = TestClient(app)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get(f"/api/businesses/{northstar}").status_code == 401
    assert client.get(f"/api/businesses/{northstar}/export").status_code == 401


def test_session_cookie_is_httponly_and_strict():
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"email": OWNER, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_logout_revokes_the_session():
    client = signed_in(OWNER)
    token = client.cookies.get("smec_session")
    assert client.post("/api/auth/logout").status_code == 204
    replay = TestClient(app, cookies={"smec_session": token})
    assert replay.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected():
    client = signed_in(OWNER)
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE auth_session SET expires_at = now() - interval '1 minute' "
                "WHERE user_id = (SELECT id FROM app_user WHERE email = :e)"
            ),
            {"e": OWNER},
        )
    assert client.get("/api/auth/me").status_code == 401


def test_me_lists_only_the_users_own_memberships(northstar):
    body = signed_in(STAFF).get("/api/auth/me").json()
    assert [m["business_id"] for m in body["memberships"]] == [northstar]
    assert body["memberships"][0]["role"] == "staff"
    assert "membership.manage" not in body["memberships"][0]["allowed_actions"]


# --- Owner settings: staff and bookkeeper cannot change roles ------------------------------------


def test_staff_cannot_change_owner_settings(northstar):
    target = membership_id(northstar, OWNER)
    r = signed_in(STAFF).patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "staff"})
    assert r.status_code == 403
    assert role_of(northstar, OWNER) == "owner"


def test_staff_cannot_promote_themselves(northstar):
    own = membership_id(northstar, STAFF)
    r = signed_in(STAFF).patch(f"/api/businesses/{northstar}/memberships/{own}", json={"role": "owner"})
    assert r.status_code == 403
    assert role_of(northstar, STAFF) == "staff"


def test_bookkeeper_cannot_change_roles(northstar):
    target = membership_id(northstar, STAFF)
    r = signed_in(BOOKKEEPER).patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "bookkeeper"})
    assert r.status_code == 403
    assert role_of(northstar, STAFF) == "staff"


def test_owner_can_change_a_role(northstar):
    target = membership_id(northstar, STAFF)
    r = signed_in(OWNER).patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "bookkeeper"})
    assert r.status_code == 200 and r.json()["role"] == "bookkeeper"
    assert role_of(northstar, STAFF) == "bookkeeper"


def test_owner_cannot_remove_the_last_owner(northstar):
    own = membership_id(northstar, OWNER)
    r = signed_in(OWNER).patch(f"/api/businesses/{northstar}/memberships/{own}", json={"role": "staff"})
    assert r.status_code == 409
    assert role_of(northstar, OWNER) == "owner"


def test_invalid_role_is_rejected(northstar):
    target = membership_id(northstar, STAFF)
    r = signed_in(OWNER).patch(f"/api/businesses/{northstar}/memberships/{target}", json={"role": "admin"})
    assert r.status_code == 422


# --- Cross-business isolation, including exports ------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "/memberships", "/export"])
def test_changing_the_business_id_reveals_nothing(northstar, riverbend, suffix):
    client = signed_in(OWNER)  # owner of Northstar, not a member of Riverbend
    assert client.get(f"/api/businesses/{northstar}{suffix}").status_code == 200
    other = client.get(f"/api/businesses/{riverbend}{suffix}")
    missing = client.get(f"/api/businesses/{uuid.uuid4()}{suffix}")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()  # an existing foreign business looks exactly like a missing one
    assert "Riverbend" not in other.text


def test_owner_cannot_change_roles_in_another_business(northstar, riverbend):
    foreign = membership_id(riverbend, OTHER_OWNER)
    client = signed_in(OWNER)
    # Foreign business in the path: not a member.
    r1 = client.patch(f"/api/businesses/{riverbend}/memberships/{foreign}", json={"role": "staff"})
    # Own business in the path, foreign membership ID in the body path: not found in this business.
    r2 = client.patch(f"/api/businesses/{northstar}/memberships/{foreign}", json={"role": "staff"})
    assert r1.status_code == r2.status_code == 404
    assert role_of(riverbend, OTHER_OWNER) == "owner"


def test_export_contains_only_the_authorized_business(northstar):
    body = signed_in(BOOKKEEPER).get(f"/api/businesses/{northstar}/export").json()
    assert body["business"]["id"] == northstar
    assert {a["external_ref"] for a in body["accounts"]} == {"ACC-BANK", "ACC-WALLET", "ACC-CASH"}
    text_body = str(body)
    assert "Riverbend" not in text_body and "RB-BANK" not in text_body
    assert "password" not in text_body and "email" not in text_body


def test_staff_cannot_export(northstar):
    assert signed_in(STAFF).get(f"/api/businesses/{northstar}/export").status_code == 403


def test_my_businesses_never_lists_foreign_businesses(riverbend):
    listed = signed_in(OWNER).get("/api/businesses").json()
    assert riverbend not in [b["business_id"] for b in listed]

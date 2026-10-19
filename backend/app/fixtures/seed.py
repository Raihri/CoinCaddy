"""Load the synthetic demo businesses, their accounts, demo users, and memberships.

Only foundation and access tables are seeded. Transactions in the Northstar fixture stay as reference
data until the ledger feature exists. Idempotent: re-running restores the demo roles and passwords.

Usage (from backend/):  python -m app.fixtures.seed
"""

import json
from pathlib import Path

from sqlalchemy import Connection, text

from app.config import REPO_ROOT, get_settings
from app.contracts.business_time import require_timezone
from app.contracts.money import Money, require_currency
from app.db import get_engine
from app.modules.identity.passwords import hash_password
from app.modules.identity.policy import ROLES

FIXTURE_PATH = REPO_ROOT / "fixtures" / "northstar_trading.json"
BUSINESS_FIXTURES = [FIXTURE_PATH, REPO_ROOT / "fixtures" / "riverbend_crafts.json"]
USERS_PATH = REPO_ROOT / "fixtures" / "demo_users.json"


def load_fixture(path: Path = FIXTURE_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_business(conn: Connection, data: dict) -> str:
    biz = data["business"]
    require_currency(biz["base_currency"])
    require_timezone(biz["timezone"])
    reserve = Money.from_json(biz["reserve"])
    business_id = conn.execute(
        text(
            """
            INSERT INTO business (slug, name, jurisdiction, base_currency, timezone,
                                  reserve_amount_minor, reserve_currency, is_synthetic)
            VALUES (:slug, :name, :jurisdiction, :base_currency, :timezone, :reserve, :reserve_ccy, true)
            ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug
            RETURNING id
            """
        ),
        {
            "slug": biz["slug"],
            "name": biz["name"],
            "jurisdiction": biz["jurisdiction"],
            "base_currency": biz["base_currency"],
            "timezone": biz["timezone"],
            "reserve": reserve.amount_minor,
            "reserve_ccy": reserve.currency,
        },
    ).scalar_one()
    for acc in data["accounts"]:
        require_currency(acc["currency"])
        conn.execute(
            text(
                """
                INSERT INTO cash_account (business_id, external_ref, label, kind, currency)
                VALUES (:bid, :ref, :label, :kind, :currency)
                ON CONFLICT (business_id, external_ref) DO NOTHING
                """
            ),
            {"bid": business_id, **{k: acc[k] for k in ("ref", "label", "kind", "currency")}},
        )
    return str(business_id)


def _seed_users(conn: Connection, business_ids: dict[str, str]) -> None:
    password_hash = hash_password(get_settings().demo_user_password)
    for u in json.loads(USERS_PATH.read_text(encoding="utf-8"))["users"]:
        user_id = conn.execute(
            text(
                """
                INSERT INTO app_user (email, display_name, password_hash, is_synthetic)
                VALUES (:email, :name, :ph, true)
                ON CONFLICT (email) DO UPDATE SET display_name = EXCLUDED.display_name,
                                                  password_hash = EXCLUDED.password_hash
                RETURNING id
                """
            ),
            {"email": u["email"].lower(), "name": u["display_name"], "ph": password_hash},
        ).scalar_one()
        for m in u["memberships"]:
            if m["role"] not in ROLES:
                raise ValueError(f"Unknown role {m['role']!r}")
            conn.execute(
                text(
                    """
                    INSERT INTO membership (business_id, user_id, role) VALUES (:b, :u, :r)
                    ON CONFLICT (business_id, user_id) DO UPDATE SET role = EXCLUDED.role, updated_at = now()
                    """
                ),
                {"b": business_ids[m["business"]], "u": user_id, "r": m["role"]},
            )


def seed() -> dict[str, str]:
    """Returns {business slug: business id}."""
    with get_engine().begin() as conn:
        ids = {}
        for path in BUSINESS_FIXTURES:
            data = load_fixture(path)
            ids[data["business"]["slug"]] = _seed_business(conn, data)
        _seed_users(conn, ids)
    return ids


if __name__ == "__main__":
    for slug, business_id in seed().items():
        print(f"Seeded synthetic business {slug} {business_id}")

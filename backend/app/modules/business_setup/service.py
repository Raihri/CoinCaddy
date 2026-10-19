"""Read model for a business profile and its accounts."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.contracts.reference import JURISDICTIONS


def business_profile(db: Session, business_id: UUID) -> dict:
    b = db.execute(
        text(
            """
            SELECT id, name, jurisdiction, base_currency, timezone, reserve_amount_minor, reserve_currency,
                   is_synthetic, version, updated_at
            FROM business WHERE id = :b
            """
        ),
        {"b": business_id},
    ).mappings().one()
    accounts = db.execute(
        text(
            """
            SELECT id, external_ref, label, kind, currency FROM cash_account
            WHERE business_id = :b ORDER BY created_at, external_ref
            """
        ),
        {"b": business_id},
    ).mappings()
    return {
        "id": str(b["id"]),
        "name": b["name"],
        "jurisdiction": b["jurisdiction"],
        "jurisdiction_name": JURISDICTIONS.get(b["jurisdiction"], (b["jurisdiction"],))[0],
        "base_currency": b["base_currency"],
        "timezone": b["timezone"],
        "reserve": {"amount_minor": b["reserve_amount_minor"], "currency": b["reserve_currency"]},
        "is_synthetic": b["is_synthetic"],
        "version": b["version"],
        "updated_at": b["updated_at"].isoformat(),
        "accounts": [
            {"id": str(a["id"]), "ref": a["external_ref"], "label": a["label"], "kind": a["kind"], "currency": a["currency"]}
            for a in accounts
        ],
    }

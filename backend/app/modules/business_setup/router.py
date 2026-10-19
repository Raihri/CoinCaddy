"""Business Setup: create a business, edit its profile and reserve, add and relabel cash accounts.

Only owners may change settings (`business.settings.update`); any signed-in user may create a new
business and becomes its owner. Every change is audited in the same transaction as the change.
Edits use optimistic concurrency: a stale `expected_version` is refused with 409.
"""

import re
import secrets
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.reference import MAX_ACCOUNTS_PER_BUSINESS, options
from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.schemas import AccountIn, AccountRelabel, BusinessCreate, SettingsUpdate
from app.modules.business_setup.service import business_profile
from app.modules.identity.deps import BusinessContext, CurrentUser, current_user, require

MAX_MEMBERSHIPS_PER_USER = 10

router = APIRouter(prefix="/api/businesses", tags=["business setup"])
reference_router = APIRouter(prefix="/api/reference", tags=["reference"])


def _validation_error(loc: list[str], msg: str) -> HTTPException:
    # Same shape as FastAPI's own 422 responses so the client handles both alike.
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, [{"loc": ["body", *loc], "msg": msg, "type": "value_error"}])


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:40] or "business"
    return f"{base}-{secrets.token_hex(3)}"


def _new_account_ref() -> str:
    return f"ACC-{secrets.token_hex(4).upper()}"


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


@reference_router.get("/settings-options")
def settings_options(_: CurrentUser = Depends(current_user)) -> dict:
    return options()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_business(body: BusinessCreate, user: CurrentUser = Depends(current_user), db: Session = Depends(get_session)) -> dict:
    count = db.execute(text("SELECT count(*) FROM membership WHERE user_id = :u"), {"u": user.id}).scalar_one()
    if count >= MAX_MEMBERSHIPS_PER_USER:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A user may belong to at most {MAX_MEMBERSHIPS_PER_USER} businesses")
    business_id = db.execute(
        text(
            """
            INSERT INTO business (slug, name, jurisdiction, base_currency, timezone,
                                  reserve_amount_minor, reserve_currency, is_synthetic)
            VALUES (:slug, :name, :jurisdiction, :base_currency, :timezone, :reserve, :reserve_ccy, false)
            RETURNING id
            """
        ),
        {
            "slug": _slug(body.name),
            "name": body.name,
            "jurisdiction": body.jurisdiction,
            "base_currency": body.base_currency,
            "timezone": body.timezone,
            "reserve": body.reserve.amount_minor,
            "reserve_ccy": body.reserve.currency,
        },
    ).scalar_one()
    db.execute(
        text("INSERT INTO membership (business_id, user_id, role) VALUES (:b, :u, 'owner')"),
        {"b": business_id, "u": user.id},
    )
    for account in body.accounts:
        _insert_account(db, business_id, account)
    record_event(
        db, action="business.create", outcome="success", actor=Actor(user.id, user.display_name, "owner"),
        business_id=business_id, target_type="business", target_id=business_id,
        details={
            "name": body.name, "jurisdiction": body.jurisdiction, "base_currency": body.base_currency,
            "timezone": body.timezone, "reserve_amount_minor": body.reserve.amount_minor,
            "account_count": len(body.accounts),
        },
    )
    db.commit()
    return business_profile(db, business_id) | {"your_role": "owner"}


@router.patch("/{business_id}/settings")
def update_settings(
    body: SettingsUpdate,
    ctx: BusinessContext = Depends(require("business.settings.update")),
    db: Session = Depends(get_session),
) -> dict:
    current = db.execute(
        text(
            """
            SELECT name, jurisdiction, base_currency, timezone, reserve_amount_minor, reserve_currency, version
            FROM business WHERE id = :b FOR UPDATE
            """
        ),
        {"b": ctx.business_id},
    ).mappings().one()
    if body.expected_version != current["version"]:
        record_event(
            db, action="business.settings_update", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id,
            target_type="business", target_id=ctx.business_id,
            details={"expected_version": body.expected_version, "current_version": current["version"]},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Settings changed since you loaded them (version {current['version']}). Reload and try again.",
        )

    requested = body.changes()
    new = {
        "name": requested.get("name", current["name"]),
        "jurisdiction": requested.get("jurisdiction", current["jurisdiction"]),
        "base_currency": requested.get("base_currency", current["base_currency"]),
        "timezone": requested.get("timezone", current["timezone"]),
        "reserve_amount_minor": requested["reserve"].amount_minor if "reserve" in requested else current["reserve_amount_minor"],
        "reserve_currency": requested["reserve"].currency if "reserve" in requested else current["reserve_currency"],
    }
    if new["reserve_currency"] != new["base_currency"]:
        if "reserve" in requested:
            raise _validation_error(["reserve", "currency"], "reserve currency must equal the base currency")
        raise _validation_error(
            ["reserve"], "changing the base currency requires restating the reserve in the new currency"
        )

    changes = {k: {"from": current[k], "to": v} for k, v in new.items() if current[k] != v}
    if not changes:
        db.rollback()
        return business_profile(db, ctx.business_id) | {"your_role": ctx.role}

    db.execute(
        text(
            """
            UPDATE business SET name = :name, jurisdiction = :jurisdiction, base_currency = :base_currency,
                   timezone = :timezone, reserve_amount_minor = :reserve_amount_minor,
                   reserve_currency = :reserve_currency, version = version + 1, updated_at = now()
            WHERE id = :b
            """
        ),
        {**new, "b": ctx.business_id},
    )
    record_event(
        db, action="business.settings_update", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
        target_type="business", target_id=ctx.business_id,
        details={"changes": changes, "version": current["version"] + 1},
    )
    db.commit()
    return business_profile(db, ctx.business_id) | {"your_role": ctx.role}


def _insert_account(db: Session, business_id: UUID, account: AccountIn) -> UUID:
    return db.execute(
        text(
            """
            INSERT INTO cash_account (business_id, external_ref, label, kind, currency, created_at)
            VALUES (:b, :ref, :label, :kind, :currency, clock_timestamp()) RETURNING id
            """
        ),
        {"b": business_id, "ref": _new_account_ref(), "label": account.label, "kind": account.kind, "currency": account.currency},
    ).scalar_one()


@router.post("/{business_id}/accounts", status_code=status.HTTP_201_CREATED)
def add_account(
    body: AccountIn,
    ctx: BusinessContext = Depends(require("business.settings.update")),
    db: Session = Depends(get_session),
) -> dict:
    # Lock the business row so concurrent adds cannot exceed the account limit.
    db.execute(text("SELECT 1 FROM business WHERE id = :b FOR UPDATE"), {"b": ctx.business_id})
    count = db.execute(text("SELECT count(*) FROM cash_account WHERE business_id = :b"), {"b": ctx.business_id}).scalar_one()
    if count >= MAX_ACCOUNTS_PER_BUSINESS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A business may have at most {MAX_ACCOUNTS_PER_BUSINESS} accounts")
    try:
        with db.begin_nested():
            account_id = _insert_account(db, ctx.business_id, body)
    except IntegrityError:
        record_event(
            db, action="account.create", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id,
            details={"label": body.label, "reason": "duplicate label"},
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this label already exists") from None
    record_event(
        db, action="account.create", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
        target_type="cash_account", target_id=account_id,
        details={"label": body.label, "kind": body.kind, "currency": body.currency},
    )
    db.commit()
    return business_profile(db, ctx.business_id) | {"your_role": ctx.role}


@router.patch("/{business_id}/accounts/{account_id}")
def relabel_account(
    account_id: UUID,
    body: AccountRelabel,
    ctx: BusinessContext = Depends(require("business.settings.update")),
    db: Session = Depends(get_session),
) -> dict:
    account = db.execute(
        text("SELECT id, label FROM cash_account WHERE business_id = :b AND id = :a FOR UPDATE"),
        {"b": ctx.business_id, "a": account_id},
    ).mappings().first()
    audit = dict(action="account.relabel", actor=_actor(ctx), business_id=ctx.business_id,
                 target_type="cash_account", target_id=account_id)
    if account is None:  # includes an account that belongs to another business
        record_event(db, outcome="not_found", **audit)
        db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if account["label"] == body.label:
        db.rollback()
        return business_profile(db, ctx.business_id) | {"your_role": ctx.role}
    try:
        with db.begin_nested():
            db.execute(
                text("UPDATE cash_account SET label = :l, updated_at = now() WHERE business_id = :b AND id = :a"),
                {"l": body.label, "b": ctx.business_id, "a": account_id},
            )
    except IntegrityError:
        record_event(db, outcome="conflict", details={"from": account["label"], "to": body.label, "reason": "duplicate label"}, **audit)
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this label already exists") from None
    record_event(db, outcome="success", details={"from": account["label"], "to": body.label}, **audit)
    db.commit()
    return business_profile(db, ctx.business_id) | {"your_role": ctx.role}

"""Sign-in for the local demo, business memberships, and the role-guarded business endpoints."""

import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts.business_time import utc_now
from app.db import get_session
from app.modules.audit.redaction import mask_email
from app.modules.audit.service import Actor, record_event
from app.modules.business_setup.service import business_profile
from app.modules.identity.deps import (
    SESSION_COOKIE,
    BusinessContext,
    CurrentUser,
    current_user,
    require,
    token_hash,
)
from app.modules.identity.passwords import DUMMY_HASH, verify_password
from app.modules.identity.policy import ROLE_MATRIX

SESSION_TTL = timedelta(hours=8)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
business_router = APIRouter(prefix="/api/businesses", tags=["business access"])


class LoginRequest(BaseModel):
    email: str
    password: str


class QuickLoginRequest(BaseModel):
    email: str


class RoleChange(BaseModel):
    role: Literal["owner", "bookkeeper", "staff"]


def _memberships(db: Session, user_id: UUID) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT b.id AS business_id, b.name AS business_name, m.role
            FROM membership m JOIN business b ON b.id = m.business_id
            WHERE m.user_id = :u ORDER BY b.name
            """
        ),
        {"u": user_id},
    ).mappings()
    result = []
    for r in rows:
        allowed = sorted(a for a, roles in ROLE_MATRIX.items() if r["role"] in roles)
        result.append({**r, "business_id": str(r["business_id"]), "allowed_actions": allowed})
    return result


def _me(db: Session, user_id: UUID, email: str, display_name: str) -> dict:
    return {
        "user": {"id": str(user_id), "email": email, "display_name": display_name},
        "memberships": _memberships(db, user_id),
    }


@auth_router.post("/login")
def login(body: LoginRequest, response: Response, db: Session = Depends(get_session)) -> dict:
    email = body.email.strip().lower()
    user = db.execute(
        text("SELECT id, email, display_name, password_hash FROM app_user WHERE email = :e"), {"e": email}
    ).mappings().first()
    password_ok = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if user is None or not password_ok:
        record_event(
            db, action="auth.sign_in", outcome="failed",
            actor=Actor(user["id"], user["display_name"]) if user else None,
            details={"email": mask_email(email)},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")

    token = secrets.token_urlsafe(32)
    session_id = db.execute(
        text("INSERT INTO auth_session (token_hash, user_id, expires_at) VALUES (:h, :u, :exp) RETURNING id"),
        {"h": token_hash(token), "u": user["id"], "exp": utc_now() + SESSION_TTL},
    ).scalar_one()
    record_event(
        db, action="auth.sign_in", outcome="success", actor=Actor(user["id"], user["display_name"]),
        target_type="session", target_id=session_id,
    )
    db.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=get_settings().app_env != "development",
        path="/api",
    )
    return _me(db, user["id"], user["email"], user["display_name"])


@auth_router.post("/quick-login")
def quick_login(body: QuickLoginRequest, response: Response, db: Session = Depends(get_session)) -> dict:
    """Passwordless sign-in is an explicit demo convenience and can only select synthetic accounts."""
    settings = get_settings()
    if not settings.demo_quick_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quick demo sign-in is unavailable")
    email = body.email.strip().lower()
    user = db.execute(
        text("SELECT id, email, display_name FROM app_user WHERE email = :e AND is_synthetic = true"), {"e": email}
    ).mappings().first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Synthetic demo user not found")

    token = secrets.token_urlsafe(32)
    session_id = db.execute(
        text("INSERT INTO auth_session (token_hash, user_id, expires_at) VALUES (:h, :u, :exp) RETURNING id"),
        {"h": token_hash(token), "u": user["id"], "exp": utc_now() + SESSION_TTL},
    ).scalar_one()
    record_event(db, action="auth.sign_in", outcome="success", actor=Actor(user["id"], user["display_name"]),
                 target_type="session", target_id=session_id, details={"method": "quick_demo"})
    db.commit()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True,
        samesite="strict", secure=settings.app_env != "development", path="/api",
    )
    return _me(db, user["id"], user["email"], user["display_name"])


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, user: CurrentUser = Depends(current_user), db: Session = Depends(get_session)) -> None:
    db.execute(text("UPDATE auth_session SET revoked_at = now() WHERE id = :s"), {"s": user.session_id})
    record_event(
        db, action="auth.sign_out", outcome="success", actor=Actor(user.id, user.display_name),
        target_type="session", target_id=user.session_id,
    )
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/api")


@auth_router.get("/me")
def me(user: CurrentUser = Depends(current_user), db: Session = Depends(get_session)) -> dict:
    return _me(db, user.id, user.email, user.display_name)


@business_router.get("")
def my_businesses(user: CurrentUser = Depends(current_user), db: Session = Depends(get_session)) -> list[dict]:
    """Only businesses the signed-in user is a member of; there is no 'list all businesses' route."""
    return _memberships(db, user.id)


@business_router.get("/{business_id}")
def get_business(ctx: BusinessContext = Depends(require("business.read")), db: Session = Depends(get_session)) -> dict:
    return business_profile(db, ctx.business_id) | {"your_role": ctx.role}


@business_router.get("/{business_id}/memberships")
def list_memberships(
    ctx: BusinessContext = Depends(require("membership.read")), db: Session = Depends(get_session)
) -> list[dict]:
    return _member_rows(db, ctx.business_id)


@business_router.patch("/{business_id}/memberships/{membership_id}")
def change_role(
    membership_id: UUID,
    body: RoleChange,
    ctx: BusinessContext = Depends(require("membership.manage")),
    db: Session = Depends(get_session),
) -> dict:
    # Lock this business's memberships so two concurrent demotions cannot remove every owner.
    rows = db.execute(
        text(
            "SELECT m.id, m.role, u.display_name FROM membership m JOIN app_user u ON u.id = m.user_id "
            "WHERE m.business_id = :b ORDER BY m.id FOR UPDATE OF m"
        ),
        {"b": ctx.business_id},
    ).mappings().all()
    target = next((r for r in rows if r["id"] == membership_id), None)
    audit = dict(
        action="membership.role_change", actor=_actor(ctx), business_id=ctx.business_id,
        target_type="membership", target_id=membership_id,
    )
    if target is None:  # includes a membership ID that belongs to another business
        record_event(db, outcome="not_found", details={"requested_role": body.role}, **audit)
        db.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Membership not found")
    owners = sum(1 for r in rows if r["role"] == "owner")
    if target["role"] == "owner" and body.role != "owner" and owners <= 1:
        record_event(
            db, outcome="conflict", details={"member_name": target["display_name"], "from_role": "owner", "to_role": body.role, "reason": "last owner"}, **audit
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "A business must keep at least one owner")
    db.execute(
        text("UPDATE membership SET role = :r, updated_at = now() WHERE business_id = :b AND id = :m"),
        {"r": body.role, "b": ctx.business_id, "m": membership_id},
    )
    # Same transaction as the change: both commit or neither does.
    record_event(db, outcome="success", details={"member_name": target["display_name"], "from_role": target["role"], "to_role": body.role}, **audit)
    db.commit()
    return next(m for m in _member_rows(db, ctx.business_id) if m["membership_id"] == str(membership_id))


@business_router.get("/{business_id}/export")
def export_business(
    ctx: BusinessContext = Depends(require("business.export")), db: Session = Depends(get_session)
) -> dict:
    """Business-scoped JSON export. Every query is filtered by the authorized business_id."""
    accounts = db.execute(
        text(
            "SELECT external_ref, label, kind, currency FROM cash_account WHERE business_id = :b ORDER BY external_ref"
        ),
        {"b": ctx.business_id},
    ).mappings()
    export = {
        "exported_at": utc_now().isoformat(),
        "synthetic": True,
        "business": business_profile(db, ctx.business_id),
        "accounts": [dict(a) for a in accounts],
        "members": [{k: m[k] for k in ("display_name", "role")} for m in _member_rows(db, ctx.business_id)],
    }
    # Record what kind of data left, never the exported content itself.
    record_event(
        db, action="business.export", outcome="success", actor=_actor(ctx), business_id=ctx.business_id,
        target_type="business", target_id=ctx.business_id,
        details={"format": "json", "sections": ["business", "accounts", "members"],
                 "account_count": len(export["accounts"]), "member_count": len(export["members"])},
    )
    db.commit()
    return export


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _member_rows(db: Session, business_id: UUID) -> list[dict]:
    rows = db.execute(
        text(
            """
            SELECT m.id, u.display_name, u.email, m.role
            FROM membership m JOIN app_user u ON u.id = m.user_id
            WHERE m.business_id = :b
            ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'bookkeeper' THEN 1 ELSE 2 END, u.display_name
            """
        ),
        {"b": business_id},
    ).mappings()
    return [
        {"membership_id": str(r["id"]), "display_name": r["display_name"], "email": r["email"], "role": r["role"]}
        for r in rows
    ]

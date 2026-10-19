"""Request dependencies that authenticate the caller and derive business scope from the session.

Status codes: 401 when not signed in; 404 when the business is not one the user belongs to (the same
answer as for a business that does not exist, so IDs reveal nothing); 403 when the user belongs to
the business but their role is not allowed the action.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.identity.policy import is_allowed

SESSION_COOKIE = "smec_session"


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    display_name: str
    session_id: UUID


@dataclass(frozen=True)
class BusinessContext:
    user: CurrentUser
    business_id: UUID
    membership_id: UUID
    role: str
    action: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request, db: Session = Depends(get_session)) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    row = db.execute(
        text(
            """
            SELECT s.id AS session_id, u.id, u.email, u.display_name
            FROM auth_session s JOIN app_user u ON u.id = s.user_id
            WHERE s.token_hash = :h AND s.revoked_at IS NULL AND s.expires_at > now()
            """
        ),
        {"h": token_hash(token)},
    ).mappings().first()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    return CurrentUser(id=row["id"], email=row["email"], display_name=row["display_name"], session_id=row["session_id"])


def require(action: str) -> Callable[..., BusinessContext]:
    """Dependency for a route under /api/businesses/{business_id}/... guarded by `action`."""

    def dependency(
        business_id: UUID, user: CurrentUser = Depends(current_user), db: Session = Depends(get_session)
    ) -> BusinessContext:
        member = db.execute(
            text("SELECT id, role FROM membership WHERE business_id = :b AND user_id = :u"),
            {"b": business_id, "u": user.id},
        ).mappings().first()
        if member is None:
            # Logged as a user-level security event, never into the other business's history.
            record_event(
                db, action=action, outcome="not_found", actor=Actor(user.id, user.display_name),
                details={"requested_business_id": str(business_id)},
            )
            db.commit()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found")
        if not is_allowed(member["role"], action):
            record_event(
                db, action=action, outcome="denied", actor=Actor(user.id, user.display_name, member["role"]),
                business_id=business_id, details={"reason": "role not allowed"},
            )
            db.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role does not allow this action")
        return BusinessContext(user, business_id, member["id"], member["role"], action)

    dependency.required_action = action  # type: ignore[attr-defined]  # read by the route-coverage test
    return dependency

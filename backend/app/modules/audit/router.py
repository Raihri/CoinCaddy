"""Business-scoped, read-only audit history. There are deliberately no write, edit, or delete routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.identity.deps import BusinessContext, require

router = APIRouter(prefix="/api/businesses", tags=["audit"])


@router.get("/{business_id}/audit-events")
def list_audit_events(
    limit: int = Query(50, ge=1, le=100),
    before_seq: int | None = Query(None, ge=1),
    ctx: BusinessContext = Depends(require("audit.read")),
    db: Session = Depends(get_session),
) -> dict:
    rows = db.execute(
        text(
            """
            SELECT seq, id, occurred_at, actor_name, actor_role, action, outcome,
                   target_type, target_id, details, event_hash
            FROM audit_event
            WHERE business_id = :b AND (CAST(:before AS bigint) IS NULL OR seq < :before)
            ORDER BY seq DESC
            LIMIT :limit
            """
        ),
        {"b": ctx.business_id, "before": before_seq, "limit": limit + 1},
    ).mappings().all()
    page = rows[:limit]
    return {
        "events": [
            {
                **{k: r[k] for k in ("seq", "actor_name", "actor_role", "action", "outcome", "target_type", "target_id", "details")},
                "id": str(r["id"]),
                "occurred_at": r["occurred_at"].isoformat(),
                "event_hash": r["event_hash"],
            }
            for r in page
        ],
        "next_before_seq": page[-1]["seq"] if len(rows) > limit else None,
    }

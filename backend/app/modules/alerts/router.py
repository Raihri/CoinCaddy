"""Smart Alerts API: run the rules (idempotent), list alerts, and read one. Details need a signed-in member."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.alerts.service import get_alert, last_run, list_alerts, run_alerts
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, require
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/alerts", tags=["alerts"])


def _today(db: Session, business_id: UUID):
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


@router.post("/run")
def run(ctx: BusinessContext = Depends(require("alerts.run")), db: Session = Depends(get_session)) -> dict:
    """Evaluate every rule on today's accepted data. Running again with unchanged data creates nothing."""
    result = run_alerts(db, ctx.business_id, ctx.user.id, _today(db, ctx.business_id))
    record_event(db, action="alerts.run", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role),
                 business_id=ctx.business_id, target_type="alert_run", target_id=result["run_id"],
                 details={k: result[k] for k in ("created", "updated", "unchanged", "resolved")} | {"inputs_sha256": result["inputs_sha256"][:16]})
    db.commit()
    return {**result, "alerts": list_alerts(db, ctx.business_id), "last_run": last_run(db, ctx.business_id)}


@router.get("")
def alerts(state: Literal["open", "resolved", "all"] = Query("open", alias="status"), limit: int = Query(100, ge=1, le=200),
           ctx: BusinessContext = Depends(require("alerts.read")), db: Session = Depends(get_session)) -> dict:
    run_row = last_run(db, ctx.business_id)
    today = _today(db, ctx.business_id).isoformat()
    return {"alerts": list_alerts(db, ctx.business_id, state, limit), "last_run": run_row,
            "today": today, "checked_today": run_row is not None and run_row["as_of"] == today}


@router.get("/{alert_id}")
def one(alert_id: UUID, ctx: BusinessContext = Depends(require("alerts.read")), db: Session = Depends(get_session)) -> dict:
    a = get_alert(db, ctx.business_id, alert_id)
    if a is None:  # includes an alert of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return a

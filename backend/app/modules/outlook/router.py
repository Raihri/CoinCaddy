"""Cash Outlook API: create a frozen snapshot, read the latest or a specific one, and verify it."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.audit.service import Actor, record_event
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.engine import compute
from app.modules.outlook.service import canonical, create_snapshot, freeze_inputs, load_snapshot, sha256
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/cash-outlook", tags=["cash outlook"])


def _today(db: Session, business_id: UUID):
    return business_today(db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": business_id}).scalar_one())


def _with_status(db: Session, business_id: UUID, snap: dict) -> dict:
    """Is the snapshot still current? Today's inputs are frozen again and compared by hash; nothing is stored."""
    today = _today(db, business_id)
    current = sha256(freeze_inputs(db, business_id, today))
    return snap | {"current": {"today": today.isoformat(), "is_today": snap["as_of"] == today.isoformat(),
                               "inputs_unchanged": snap["as_of"] == today.isoformat() and snap["inputs_sha256"] == current}}


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
def new_snapshot(ctx: BusinessContext = Depends(require("outlook.run")), db: Session = Depends(get_session)) -> dict:
    sid = create_snapshot(db, ctx.business_id, ctx.user.id, _today(db, ctx.business_id))
    snap = load_snapshot(db, ctx.business_id, sid)
    base = next((c for c in snap["results"]["currencies"] if c["reserve_minor"] is not None), None)
    record_event(db, action="outlook.snapshot", outcome="success", actor=Actor(ctx.user.id, ctx.user.display_name, ctx.role),
                 business_id=ctx.business_id, target_type="forecast_snapshot", target_id=sid,
                 details={"calc_version": snap["calc_version"], "as_of": snap["as_of"], "inputs_sha256": snap["inputs_sha256"][:16],
                          "expected_breach": (base or {}).get("first_breach", {}).get("expected") is not None,
                          "cautious_breach": (base or {}).get("first_breach", {}).get("cautious") is not None})
    db.commit()
    return _with_status(db, ctx.business_id, snap)


@router.get("/latest")
def latest(ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    snap = load_snapshot(db, ctx.business_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No outlook has been calculated yet")
    return _with_status(db, ctx.business_id, snap)


@router.get("/snapshots")
def list_snapshots(ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> list[dict]:
    rows = db.execute(text(
        """SELECT s.id, s.as_of, s.calc_version, s.inputs_sha256, s.created_at, u.display_name FROM forecast_snapshot s
           JOIN app_user u ON u.id = s.created_by WHERE s.business_id = :b ORDER BY s.created_at DESC LIMIT 50"""),
        {"b": ctx.business_id}).mappings().all()
    return [{"id": str(r["id"]), "as_of": r["as_of"].isoformat(), "calc_version": r["calc_version"],
             "inputs_sha256": r["inputs_sha256"], "created_at": r["created_at"].isoformat(), "created_by": r["display_name"]} for r in rows]


@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: UUID, ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    snap = load_snapshot(db, ctx.business_id, snapshot_id)
    if snap is None:  # includes a snapshot of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Snapshot not found")
    return _with_status(db, ctx.business_id, snap)


@router.get("/snapshots/{snapshot_id}/verify")
def verify(snapshot_id: UUID, ctx: BusinessContext = Depends(require("records.read")), db: Session = Depends(get_session)) -> dict:
    """Recompute the stored results from the stored inputs: proves the figures follow from the frozen inputs."""
    snap = load_snapshot(db, ctx.business_id, snapshot_id)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Snapshot not found")
    return {"snapshot_id": snap["id"], "calc_version": snap["calc_version"],
            "inputs_hash_matches": sha256(snap["inputs"]) == snap["inputs_sha256"],
            "results_reproduce": canonical(compute(snap["inputs"])) == canonical(snap["results"])}

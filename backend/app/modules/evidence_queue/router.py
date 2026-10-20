"""Evidence queue API: read-only view of the verification tasks for one saved Payment Review."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.modules.evidence_queue.engine import build_queue
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.service import freeze_inputs
from app.modules.records.service import business_today

router = APIRouter(prefix="/api/businesses/{business_id}/payment-reviews/{review_id}/evidence-queue", tags=["evidence queue"])


@router.get("")
def queue(review_id: UUID, ctx: BusinessContext = Depends(require("paymentreview.read")), db: Session = Depends(get_session)) -> dict:
    """Compute the queue from the saved review's frozen inputs. Nothing is written and no record or review changes."""
    row = db.execute(text("SELECT id, request, inputs, results, decision, as_of FROM payment_review WHERE business_id = :b AND id = :i"),
                     {"b": ctx.business_id, "i": review_id}).mappings().first()
    if row is None:  # includes a review of another business
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment review not found")
    tz = db.execute(text("SELECT timezone FROM business WHERE id = :b"), {"b": ctx.business_id}).scalar_one()
    now = freeze_inputs(db, ctx.business_id, business_today(tz))
    return {"review_id": str(row["id"]), "as_of": row["as_of"].isoformat(), **build_queue(row["inputs"], row["request"], row["results"], now)}

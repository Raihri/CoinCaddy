"""Reading sandbox executions and their log. Kept free of the approvals router so the approval inbox can show whether a
proposal was executed without an import cycle."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.approvals.policy import iso
from app.modules.executor.adapters import ADAPTERS
from app.modules.outlook.service import sha256

FINAL = ("succeeded", "rejected")


def mock_key(business_id, proposal_id, payload_sha256: str) -> str:
    """The key sent to the mock service. It depends only on the proposal, so the sandbox processes a proposal at most
    once, even if a caller loses its own idempotency key and tries again with a new one."""
    return sha256({"business_id": str(business_id), "proposal_id": str(proposal_id), "payload_sha256": payload_sha256})


_LSEL = """SELECT l.*, u.display_name AS actor_name, p.kind, p.tier FROM sandbox_execution_log l
           JOIN app_user u ON u.id = l.actor_id JOIN action_proposal p ON p.id = l.proposal_id WHERE l.business_id = :b"""


def log_out(r) -> dict:
    a = ADAPTERS.get(r["adapter"]) if r["adapter"] else None
    return {"id": str(r["id"]), "seq": r["seq"], "event": r["event"], "proposal_id": str(r["proposal_id"]), "kind": r["kind"], "tier": r["tier"],
            "execution_id": str(r["execution_id"]) if r["execution_id"] else None, "adapter": r["adapter"], "adapter_label": a.label if a else None,
            "idempotency_key": r["idempotency_key"], "actor": r["actor_name"], "actor_role": r["actor_role"], "at": iso(r["created_at"]),
            "request": r["request"], "result": r["result"], "checks": r["checks"], "http_status": r["http_status"], "reference": r["reference"]}


def log_page(db: Session, b: UUID, limit: int, before_seq: int | None = None, proposal_id: UUID | None = None) -> dict:
    q = _LSEL + (" AND l.seq < :before" if before_seq else "") + (" AND l.proposal_id = :p" if proposal_id else "") + " ORDER BY l.seq DESC LIMIT :n"
    rows = db.execute(text(q), {"b": b, "before": before_seq, "p": proposal_id, "n": limit + 1}).mappings().all()
    items = [log_out(r) for r in rows[:limit]]
    return {"items": items, "next_before_seq": items[-1]["seq"] if len(rows) > limit else None}


def execution_view(db: Session, b: UUID, execution_id: UUID) -> dict | None:
    e = db.execute(text("""SELECT e.*, u.display_name AS created_by_name FROM sandbox_execution e JOIN app_user u ON u.id = e.created_by
                           WHERE e.business_id = :b AND e.id = :i"""), {"b": b, "i": execution_id}).mappings().first()
    if e is None:
        return None
    log = [log_out(r) for r in db.execute(text(_LSEL + " AND l.execution_id = :i ORDER BY l.seq"), {"b": b, "i": execution_id}).mappings()]
    final = next((x for x in log if x["event"] in FINAL), None)
    last = next((x for x in reversed(log) if x["event"] in FINAL + ("failed",)), None)
    dispatched = [x for x in log if x["event"] == "dispatched"]
    a = ADAPTERS[e["adapter"]]
    return {"id": str(e["id"]), "proposal_id": str(e["proposal_id"]), "adapter": e["adapter"], "adapter_label": a.label, "endpoint": a.path,
            "idempotency_key": e["idempotency_key"], "payload_sha256": e["payload_sha256"], "request_sha256": e["request_sha256"],
            "created_by": e["created_by_name"], "created_at": iso(e["created_at"]),
            "outcome": final["event"] if final else (last["event"] if last else "dispatched"), "final": final is not None,
            "reference": final["reference"] if final else None, "result": (final or last or {}).get("result"),
            "request": dispatched[0]["request"] if dispatched else None, "attempts": len(dispatched),
            "replays": sum(1 for x in log if x["event"] == "replayed"), "completed_at": final["at"] if final else None,
            "log": log, "sandbox": True}


def summaries(db: Session, b: UUID, proposal_ids: list) -> dict:
    """{proposal_id: {execution_id, outcome, reference, at} or None} for the approval inbox."""
    out: dict = {p: None for p in proposal_ids}
    if proposal_ids:
        for r in db.execute(text(
                """SELECT e.id, e.proposal_id, e.adapter,
                          (SELECT l.event FROM sandbox_execution_log l WHERE l.execution_id = e.id AND l.event IN ('succeeded', 'rejected', 'failed')
                           ORDER BY l.seq DESC LIMIT 1) AS last_event,
                          (SELECT l.reference FROM sandbox_execution_log l WHERE l.execution_id = e.id AND l.event = 'succeeded') AS reference,
                          (SELECT l.created_at FROM sandbox_execution_log l WHERE l.execution_id = e.id AND l.event IN ('succeeded', 'rejected')) AS done_at
                   FROM sandbox_execution e WHERE e.business_id = :b AND e.proposal_id = ANY(:ids)"""),
                {"b": b, "ids": proposal_ids}).mappings():
            out[r["proposal_id"]] = {"execution_id": str(r["id"]), "adapter": r["adapter"], "outcome": r["last_event"] or "dispatched",
                                     "reference": r["reference"], "completed_at": iso(r["done_at"]) if r["done_at"] else None}
    return out

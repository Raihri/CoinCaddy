"""Sandbox Action Executor API: run an already approved and released proposal through its sandbox adapter, once.

Before anything is sent, every gate is checked again at that moment: the approval and the release, the business scope,
the payload hash, the expiry, the source records, and (for a payment) a fresh Fraud Monitor scan and its holds. A
failed check sends nothing and is logged as blocked. The caller's idempotency key binds one execution to one
proposal: a retry with the same key returns the stored outcome without calling the adapter again, and a proposal can
be executed only once. No proposal types or approval rules are defined here; the executor only consumes them.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.db import get_session
from app.modules.approvals.policy import proposal_hash, state
from app.modules.approvals.router import HASH, _decisions, _load, release_checks
from app.modules.audit.service import Actor, record_event
from app.modules.executor import service
from app.modules.executor.adapters import ADAPTERS, BY_KIND
from app.modules.fraud.service import run_scan
from app.modules.identity.deps import BusinessContext, require
from app.modules.outlook.service import canonical, sha256

router = APIRouter(prefix="/api/businesses/{business_id}/sandbox", tags=["sandbox executor"])
KEY = r"^[A-Za-z0-9_-]{16,100}$"


class ExecuteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    payload_sha256: StrictStr = Field(pattern=HASH)
    idempotency_key: StrictStr = Field(pattern=KEY)
    confirm_sandbox: StrictBool

    @field_validator("confirm_sandbox")
    @classmethod
    def confirmed(cls, v: bool) -> bool:
        if not v:
            raise ValueError("confirm that this runs only in the sandbox")
        return v


def _actor(ctx: BusinessContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.display_name, ctx.role)


def _log(db: Session, ctx: BusinessContext, row, event: str, key: str, execution_id=None, adapter: str | None = None, **cols) -> None:
    db.execute(text(
        """INSERT INTO sandbox_execution_log (business_id, proposal_id, execution_id, event, adapter, idempotency_key, actor_id, actor_role,
                                              request, result, checks, http_status, reference)
           VALUES (:b, :p, :e, :ev, :a, :k, :u, :r, CAST(:req AS jsonb), CAST(:res AS jsonb), CAST(:c AS jsonb), :hs, :ref)"""),
        {"b": ctx.business_id, "p": row["id"], "e": execution_id, "ev": event, "a": adapter, "k": key, "u": ctx.user.id, "r": ctx.role,
         "req": canonical(cols["request"]) if cols.get("request") is not None else None,
         "res": canonical(cols["result"]) if cols.get("result") is not None else None,
         "c": canonical(cols["checks"]) if cols.get("checks") is not None else None,
         "hs": cols.get("http_status"), "ref": cols.get("reference")})


def _block(db: Session, ctx: BusinessContext, row, key: str, checks: list[dict]) -> None:
    """Log a refused attempt (nothing was sent), audit it, and answer 409 with every check."""
    failed = [c for c in checks if not c["ok"]]
    adapter = BY_KIND.get(row["kind"])
    _log(db, ctx, row, "blocked", key, adapter=adapter.code if adapter else None, checks=checks)
    record_event(db, action="executor.execute", outcome="conflict", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                 target_id=row["id"], details={"kind": row["kind"], "tier": row["tier"], "failed_checks": [c["code"] for c in failed]})
    db.commit()
    raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not executed: " + "; ".join(c["detail"] for c in failed), "checks": checks})


def _destination(db: Session, b: UUID, payload: dict) -> dict | None:
    """The supplier's current payment destination (masked); the app never stores the full number."""
    r = db.execute(text(
        """SELECT d.id, d.kind, d.institution, d.last4 FROM payee_destination d JOIN obligation o ON o.business_id = d.business_id AND o.party_id = d.party_id
           WHERE d.business_id = :b AND o.id = :o ORDER BY d.created_at DESC, d.id DESC LIMIT 1"""),
        {"b": b, "o": payload["obligation_id"]}).mappings().first()
    return {"id": str(r["id"]), "kind": r["kind"], "institution": r["institution"], "last4": r["last4"]} if r else None


def _checks(db: Session, ctx: BusinessContext, row, decisions: list, seen_hash: str, now, destination: dict | None) -> list[dict]:
    released = next((d for d in decisions if d["event"] == "executable"), None)
    checks = [{"code": "released", "ok": released is not None and released["payload_sha256"] == row["payload_sha256"],
               "detail": "Approved and released as executable with this exact hash" if released else
                         f"Not released as executable (the proposal is {state(row, decisions, now)})"},
              {"code": "business_scope", "ok": str(row["business_id"]) == str(ctx.business_id) and proposal_hash(row) == row["payload_sha256"],
               "detail": "The proposal, its hash envelope, and your session name the same business"}]
    checks += release_checks(db, ctx, row, decisions, seen_hash, now)
    adapter = BY_KIND.get(row["kind"])
    checks.append({"code": "adapter", "ok": adapter is not None,
                   "detail": f"{adapter.label} ({adapter.path})" if adapter else "No sandbox adapter handles this action"})
    if row["kind"] == "supplier_payment":
        checks.append({"code": "destination_on_file", "ok": destination is not None,
                       "detail": f"Pays {destination['institution']} ••••{destination['last4']} (the supplier's current destination)" if destination
                       else "No bank or wallet destination is recorded for this supplier; record one in the Fraud Monitor first"})
    return checks


@router.get("/adapters")
def adapters(ctx: BusinessContext = Depends(require("executor.read"))) -> dict:
    return {"mode": "sandbox", "credentials": "none", "base": "MOCK_SERVICES_URL (local mock-service boundary only)",
            "adapters": [{"code": a.code, "label": a.label, "endpoint": a.path, "proposal_kind": a.proposal_kind, "available": a.available,
                          "note": a.note} for a in ADAPTERS.values()]}


@router.get("/executions")
def list_log(limit: Annotated[int, Query(ge=1, le=100)] = 20, before_seq: Annotated[int | None, Query(ge=1)] = None,
             proposal_id: UUID | None = None, ctx: BusinessContext = Depends(require("executor.read")), db: Session = Depends(get_session)) -> dict:
    return service.log_page(db, ctx.business_id, limit, before_seq, proposal_id)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: UUID, ctx: BusinessContext = Depends(require("executor.read")), db: Session = Depends(get_session)) -> dict:
    view = service.execution_view(db, ctx.business_id, execution_id)
    if view is None:  # includes another business's execution
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Execution not found")
    return view


@router.post("/executions", status_code=status.HTTP_201_CREATED)
def execute(body: ExecuteIn, response: Response, ctx: BusinessContext = Depends(require("executor.run")), db: Session = Depends(get_session)) -> dict:
    row = _load(db, ctx, body.proposal_id, lock=True)   # 404 for another business's proposal; the lock serialises retries
    key = body.idempotency_key
    by_key = db.execute(text("SELECT * FROM sandbox_execution WHERE business_id = :b AND idempotency_key = :k"),
                        {"b": ctx.business_id, "k": key}).mappings().first()
    existing = db.execute(text("SELECT * FROM sandbox_execution WHERE proposal_id = :p"), {"p": row["id"]}).mappings().first()
    if by_key is not None and by_key["proposal_id"] != row["id"]:
        _block(db, ctx, row, key, [{"code": "idempotency_key", "ok": False, "detail": "This idempotency key was already used for another proposal"}])
    if existing is not None and existing["idempotency_key"] != key:
        _block(db, ctx, row, key, [{"code": "idempotency_key", "ok": False,
                                    "detail": "This proposal was already sent with another idempotency key; an action runs once"}])

    if existing is not None:
        view = service.execution_view(db, ctx.business_id, existing["id"])
        if view["final"]:   # a retry: return the stored outcome without calling the adapter again
            _log(db, ctx, row, "replayed", key, existing["id"], existing["adapter"], result={"outcome": view["outcome"], "reference": view["reference"]},
                 reference=view["reference"])
            record_event(db, action="executor.replay", outcome="success", actor=_actor(ctx), business_id=ctx.business_id, target_type="action_proposal",
                         target_id=row["id"], details={"kind": row["kind"], "adapter": existing["adapter"], "outcome": view["outcome"], "reference": view["reference"]})
            db.commit()
            response.status_code = status.HTTP_200_OK
            return service.execution_view(db, ctx.business_id, existing["id"]) | {"replayed": True}

    if row["kind"] == "supplier_payment":   # a destination change or a duplicate may have appeared since release
        run_scan(db, ctx.business_id, ctx.user.id, "execution", row["id"])
    now = utc_now()
    decisions = _decisions(db, [row["id"]])[row["id"]]
    destination = _destination(db, ctx.business_id, row["payload"]) if row["kind"] == "supplier_payment" else None
    checks = _checks(db, ctx, row, decisions, body.payload_sha256, now, destination)
    if not all(c["ok"] for c in checks):
        _block(db, ctx, row, key, checks)

    adapter = BY_KIND[row["kind"]]
    request = adapter.build(row["payload"], {"destination": destination})
    mk = service.mock_key(ctx.business_id, row["id"], row["payload_sha256"])
    if existing is None:
        try:
            execution_id = db.execute(text(
                """INSERT INTO sandbox_execution (business_id, proposal_id, idempotency_key, adapter, payload_sha256, mock_key, request_sha256, created_by, created_role)
                   VALUES (:b, :p, :k, :a, :h, :mk, :rh, :u, :r) RETURNING id"""),
                {"b": ctx.business_id, "p": row["id"], "k": key, "a": adapter.code, "h": row["payload_sha256"], "mk": mk, "rh": sha256(request),
                 "u": ctx.user.id, "r": ctx.role}).scalar_one()
        except IntegrityError as exc:   # the same key used at the same moment for another proposal
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "This idempotency key is already in use. Use a new key for a new action.") from exc
    else:
        execution_id = existing["id"]   # the earlier attempt got no answer: send again; the mock key makes it one outcome
        response.status_code = status.HTTP_200_OK
    _log(db, ctx, row, "dispatched", key, execution_id, adapter.code, request=adapter.redacted(request), checks=checks)
    out = adapter.send(request, mk)
    _log(db, ctx, row, out.event, key, execution_id, adapter.code, result=out.result, http_status=out.http_status, reference=out.reference)
    record_event(db, action="executor.execute", outcome="success" if out.event == "succeeded" else "failed", actor=_actor(ctx),
                 business_id=ctx.business_id, target_type="action_proposal", target_id=row["id"],
                 details={"kind": row["kind"], "tier": row["tier"], "adapter": adapter.code, "outcome": out.event, "reference": out.reference,
                          "checks_passed": len(checks), "payload_sha256": row["payload_sha256"][:16], "attempt": "retry" if existing else "first"})
    db.commit()
    return service.execution_view(db, ctx.business_id, execution_id) | {"replayed": False}


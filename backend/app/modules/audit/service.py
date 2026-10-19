"""Append audit events and verify the hash chain.

record_event() writes inside the caller's transaction, so a successful change and its audit event
commit or roll back together. For refused requests the caller commits right after recording, before
raising the HTTP error.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from app.contracts.business_time import utc_now
from app.modules.audit.redaction import redact

GENESIS_HASH = "0" * 64
# Serializes appends so each event links to the one committed before it.
_CHAIN_LOCK_KEY = 0x5ECA0D17

OUTCOMES = ("success", "denied", "not_found", "conflict", "failed")


@dataclass(frozen=True)
class Actor:
    user_id: UUID | None
    name: str | None
    role: str | None = None


def _canonical(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def compute_hash(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + _canonical(event)).hexdigest()


def _hashed_fields(
    *, id: str, occurred_at: datetime, business_id: str | None, actor_user_id: str | None,
    actor_name: str | None, actor_role: str | None, action: str, outcome: str,
    target_type: str | None, target_id: str | None, details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": id,
        "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
        "business_id": business_id,
        "actor_user_id": actor_user_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "action": action,
        "outcome": outcome,
        "target_type": target_type,
        "target_id": target_id,
        "details": details,
    }


def record_event(
    db: Session | Connection,
    *,
    action: str,
    outcome: str,
    actor: Actor | None,
    business_id: UUID | str | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown audit outcome {outcome!r}")
    fields = _hashed_fields(
        id=str(uuid.uuid4()),
        occurred_at=utc_now(),
        business_id=str(business_id) if business_id else None,
        actor_user_id=str(actor.user_id) if actor and actor.user_id else None,
        actor_name=actor.name if actor else None,
        actor_role=actor.role if actor else None,
        action=action,
        outcome=outcome,
        target_type=target_type,
        target_id=str(target_id) if target_id else None,
        details=redact(details or {}),
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    prev = db.execute(text("SELECT event_hash FROM audit_event ORDER BY seq DESC LIMIT 1")).scalar_one_or_none()
    prev_hash = prev or GENESIS_HASH
    event_hash = compute_hash(prev_hash, fields)
    db.execute(
        text(
            """
            INSERT INTO audit_event (id, occurred_at, business_id, actor_user_id, actor_name, actor_role,
                                     action, outcome, target_type, target_id, details, prev_hash, event_hash)
            VALUES (:id, :occurred_at, :business_id, :actor_user_id, :actor_name, :actor_role,
                    :action, :outcome, :target_type, :target_id, CAST(:details AS jsonb), :prev_hash, :event_hash)
            """
        ),
        {
            **fields,
            "occurred_at": datetime.fromisoformat(fields["occurred_at"]),
            "details": json.dumps(fields["details"]),
            "prev_hash": prev_hash,
            "event_hash": event_hash,
        },
    )
    return fields["id"]


def verify_chain(db: Session | Connection) -> tuple[bool, int, int | None]:
    """Recompute every hash in order. Returns (ok, events_checked, first_bad_seq)."""
    rows = db.execute(text("SELECT * FROM audit_event ORDER BY seq")).mappings()
    prev_hash, count = GENESIS_HASH, 0
    for r in rows:
        count += 1
        fields = _hashed_fields(
            id=str(r["id"]),
            occurred_at=r["occurred_at"],
            business_id=str(r["business_id"]) if r["business_id"] else None,
            actor_user_id=str(r["actor_user_id"]) if r["actor_user_id"] else None,
            actor_name=r["actor_name"],
            actor_role=r["actor_role"],
            action=r["action"],
            outcome=r["outcome"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            details=r["details"],
        )
        if r["prev_hash"] != prev_hash or r["event_hash"] != compute_hash(prev_hash, fields):
            return False, count, r["seq"]
        prev_hash = r["event_hash"]
    return True, count, None

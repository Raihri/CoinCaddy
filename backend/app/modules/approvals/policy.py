"""Approval policy `approval-policy-v1`: which actions can be proposed, their tier and gates, and the proposal hash.

T2 (low-impact sandbox action) needs an explicit owner approval. T3 (payment, application submission) also needs the
approving owner to re-enter their password and record how the action was verified, and always needs a second
reviewer: the person who proposed it cannot approve it. Approval and release are owner-only.
"""

from datetime import datetime, timedelta, timezone

from app.modules.outlook.service import sha256

POLICY_VERSION = "approval-policy-v1"
REQUIRED_ROLE = "owner"
REAUTH_MAX_FAILURES = 5
REAUTH_WINDOW = timedelta(minutes=15)

KINDS: dict[str, dict] = {
    "invoice_reminder": {"tier": "T2", "title": "Send overdue invoice reminder"},
    "supplier_payment": {"tier": "T3", "title": "Pay supplier (sandbox)"},
    "financing_application": {"tier": "T3", "title": "Submit financing application (sandbox lender)"},
    "funding_packet_share": {"tier": "T3", "title": "Share Funding Passport (sandbox)"},
}

TIERS: dict[str, dict] = {
    "T2": {"second_reviewer_required": False, "default_hours": 48, "max_hours": 72,
           "gates": ["Explicit owner approval of this exact proposal"]},
    "T3": {"second_reviewer_required": True, "default_hours": 24, "max_hours": 24,
           "gates": ["Explicit owner approval of this exact proposal", "Owner re-authentication (password) when approving",
                     "Documented verification of the payee, amount, or recipient", "A second reviewer: the proposer cannot approve"]},
}

VERIFICATION_METHODS = {
    "called_known_contact": "Called the counterparty on a contact already on file",
    "met_in_person": "Confirmed in person",
    "written_confirmation": "Written confirmation kept on file",
}


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def envelope(*, business_id, kind: str, tier: str, required_role: str, second_reviewer_required: bool, policy_version: str,
             proposed_by, created_at: datetime, expires_at: datetime, payload: dict) -> dict:
    """Everything an approval is bound to. Changing any of it (payload, tier, role, expiry, proposer) changes the hash."""
    return {"business_id": str(business_id), "kind": kind, "tier": tier, "required_role": required_role,
            "second_reviewer_required": second_reviewer_required, "policy_version": policy_version, "proposed_by": str(proposed_by),
            "created_at": iso(created_at), "expires_at": iso(expires_at), "payload": payload}


def proposal_hash(row) -> str:
    """Recompute the hash of a stored proposal row (a mapping with the action_proposal columns)."""
    return sha256(envelope(business_id=row["business_id"], kind=row["kind"], tier=row["tier"], required_role=row["required_role"],
                           second_reviewer_required=row["second_reviewer_required"], policy_version=row["policy_version"],
                           proposed_by=row["proposed_by"], created_at=row["created_at"], expires_at=row["expires_at"], payload=row["payload"]))


def state(row, decisions: list, now: datetime) -> str:
    """pending -> approved -> executable, or rejected / withdrawn; pending or approved past the expiry -> expired."""
    events = {d["event"] for d in decisions}
    if "executable" in events:
        return "executable"
    if "rejected" in events:
        return "rejected"
    if "withdrawn" in events:
        return "withdrawn"
    if now >= row["expires_at"]:
        return "expired"
    return "approved" if "approved" in events else "pending"

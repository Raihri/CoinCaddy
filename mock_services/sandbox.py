"""Sandbox endpoints behind the mock-service boundary: one per external action, each with its own request contract.

- POST /sandbox/reminders     mock reminder send (nothing is emailed or texted)
- POST /sandbox/applications  mock financing application submission (no lender sees it; no decision is made)
- POST /sandbox/payments      mock payment initiation (no money moves)
- POST /sandbox/packets       mock packet sharing (nothing leaves this machine)

None of them needs or accepts credentials. Every request must carry an `Idempotency-Key` header (64 hex characters).
The first request with a key is processed and its response stored; a repeat with the same key and the same body gets
the stored response with `replayed: true`, and a repeat with a different body is refused. The store is in memory, so
it lasts until this process restarts. `GET /sandbox/requests` lists what was received, for evidence.
"""

import hashlib
import json
import threading
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

KEY_PATTERN = r"^[0-9a-f]{64}$"
# The sandbox bank refuses a single payment above BDT 5,00,000.00, so a rejected outcome can be demonstrated.
PAYMENT_LIMIT_MINOR = 50_000_000
SUPPORTED_CURRENCIES = {"BDT"}

_lock = threading.Lock()
_store: dict[str, dict] = {}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReminderRequest(_Strict):
    reference: StrictStr = Field(min_length=1, max_length=40)
    customer: StrictStr = Field(min_length=1, max_length=200)
    channel: Literal["email", "sms"]
    amount_minor: StrictInt = Field(gt=0)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    due_date: StrictStr = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    message: StrictStr = Field(min_length=10, max_length=500)


class ApplicationRequest(_Strict):
    product_id: StrictStr = Field(min_length=1, max_length=80)
    product: StrictStr = Field(min_length=1, max_length=200)
    provider: StrictStr = Field(min_length=1, max_length=200)
    amount_minor: StrictInt = Field(gt=0)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    purpose: StrictStr = Field(min_length=1, max_length=80)
    needed_by: StrictStr | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    illustrative: StrictBool


class Destination(_Strict):
    kind: Literal["bank", "mobile_wallet"]
    institution: StrictStr = Field(min_length=2, max_length=80)
    last4: StrictStr = Field(pattern=r"^\d{4}$")


class PaymentRequest(_Strict):
    reference: StrictStr = Field(min_length=1, max_length=40)
    payee: StrictStr = Field(min_length=1, max_length=200)
    destination: Destination
    from_account: StrictStr = Field(min_length=1, max_length=80)
    amount_minor: StrictInt = Field(gt=0)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")


class PacketRequest(_Strict):
    packet_id: StrictStr = Field(min_length=1, max_length=80)
    recipient: StrictStr = Field(min_length=1, max_length=200)
    fields: list[StrictStr] = Field(min_length=1, max_length=50)
    expires_at: StrictStr = Field(min_length=10, max_length=40)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _once(endpoint: str, key: str, body: BaseModel, process) -> JSONResponse:
    """Process a request once per idempotency key; replay the stored response for an identical repeat."""
    body_hash = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    with _lock:
        seen = _store.get(key)
        if seen is not None:
            if seen["endpoint"] != endpoint or seen["body_sha256"] != body_hash:
                raise HTTPException(422, "This idempotency key was already used for a different request.")
            seen["times_seen"] += 1
            return JSONResponse(seen["response"] | {"replayed": True}, status_code=seen["status"])
        status, response = process()
        response = response | {"sandbox": True, "received_at": _now(), "idempotency_key": key}
        _store[key] = {"endpoint": endpoint, "body_sha256": body_hash, "status": status, "response": response,
                       "times_seen": 1, "first_seen": response["received_at"]}
    return JSONResponse(response | {"replayed": False}, status_code=status)


def _ref(prefix: str, key: str) -> str:
    return f"SBX-{prefix}-{key[:10].upper()}"


IdemKey = Header(alias="Idempotency-Key", pattern=KEY_PATTERN)


@router.post("/reminders")
def send_reminder(body: ReminderRequest, key: str = IdemKey) -> JSONResponse:
    return _once("reminders", key, body, lambda: (200, {
        "reference": _ref("RMD", key), "status": "queued", "delivered": False,
        "note": f"Sandbox only: no {'email' if body.channel == 'email' else 'SMS'} was sent to {body.customer}."}))


@router.post("/applications")
def submit_application(body: ApplicationRequest, key: str = IdemKey) -> JSONResponse:
    return _once("applications", key, body, lambda: (200, {
        "reference": _ref("APP", key), "status": "received", "lender_decision": None,
        "note": f"Sandbox only: {body.provider} did not receive this application and no credit decision was made."}))


@router.post("/payments")
def initiate_payment(body: PaymentRequest, key: str = IdemKey) -> JSONResponse:
    def process():
        if body.currency not in SUPPORTED_CURRENCIES:
            return 422, {"reference": None, "status": "rejected", "code": "currency_not_supported", "money_moved": False,
                         "note": f"The sandbox bank only accepts {', '.join(sorted(SUPPORTED_CURRENCIES))}."}
        if body.amount_minor > PAYMENT_LIMIT_MINOR:
            return 422, {"reference": None, "status": "rejected", "code": "over_sandbox_limit", "money_moved": False,
                         "note": "The sandbox bank refuses a single payment above BDT 5,00,000.00."}
        return 200, {"reference": _ref("PAY", key), "status": "accepted", "money_moved": False,
                     "note": f"Sandbox only: no money moved to {body.payee} ({body.destination.institution} ••••{body.destination.last4})."}
    return _once("payments", key, body, process)


@router.post("/packets")
def share_packet(body: PacketRequest, key: str = IdemKey) -> JSONResponse:
    return _once("packets", key, body, lambda: (200, {
        "reference": _ref("PKT", key), "status": "shared", "fields_received": len(body.fields),
        "note": f"Sandbox only: nothing was sent to {body.recipient}."}))


@router.get("/requests")
def received(request: Request) -> dict:
    """What the sandbox received, without request bodies: endpoint, key, status, and how many times it was seen."""
    with _lock:
        items = [{"idempotency_key": k, "endpoint": v["endpoint"], "status": v["status"], "reference": v["response"].get("reference"),
                  "times_seen": v["times_seen"], "first_seen": v["first_seen"]} for k, v in _store.items()]
    key = request.query_params.get("key")
    return {"items": [i for i in items if not key or i["idempotency_key"] == key]}

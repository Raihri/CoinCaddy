"""Separate local adapters for the four sandbox actions. Each builds the minimum request its mock endpoint needs from an
approved payload, says how that request is redacted for the log, and sends it to its own endpoint on the mock-service
boundary. No adapter holds or sends credentials, and none can reach a real bank, lender, or messaging provider: the
only base URL is MOCK_SERVICES_URL.
"""

import hashlib
from dataclasses import dataclass, field

import httpx

from app.config import get_settings
from app.modules.audit.redaction import redact

TIMEOUT_SECONDS = 5.0


def mock_client() -> httpx.Client:
    """The only way out of the API to the sandbox. Tests replace it with an in-process client for the mock app."""
    return httpx.Client(base_url=get_settings().mock_services_url, timeout=TIMEOUT_SECONDS)


@dataclass
class Outcome:
    event: str                     # succeeded | rejected (final) | failed (no answer; the same key may retry)
    http_status: int | None
    reference: str | None
    result: dict = field(default_factory=dict)


class Adapter:
    code: str
    label: str
    path: str
    proposal_kind: str
    available: bool = True
    note: str = ""
    # Fields of the mock's answer kept in the log.
    result_fields: tuple[str, ...] = ("reference", "status", "note", "sandbox", "received_at")

    def build(self, payload: dict, extra: dict) -> dict:
        raise NotImplementedError

    def redacted(self, request: dict) -> dict:
        return redact(request)

    def send(self, request: dict, mock_key: str) -> Outcome:
        client = mock_client()
        try:
            r = client.post(self.path, json=request, headers={"Idempotency-Key": mock_key})
        except httpx.HTTPError as exc:
            return Outcome("failed", None, None, {"error": type(exc).__name__, "note": "The sandbox did not answer. Retry with the same key."})
        finally:
            client.close()
        try:
            data = r.json()
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        if r.status_code >= 500:
            return Outcome("failed", r.status_code, None, {"error": f"HTTP {r.status_code}", "note": "The sandbox failed. Retry with the same key."})
        if r.status_code >= 400:
            note = data.get("note") or (data.get("detail") if isinstance(data.get("detail"), str) else "The sandbox refused the request.")
            return Outcome("rejected", r.status_code, None, redact({"status": "rejected", "code": data.get("code") or "invalid_request", "note": str(note),
                                                                    "replayed": bool(data.get("replayed"))}))
        reference = data.get("reference")
        if not isinstance(reference, str):
            return Outcome("failed", r.status_code, None, {"error": "no reference", "note": "The sandbox answer had no reference. Retry with the same key."})
        keep = {k: v for k, v in data.items() if k in self.result_fields}
        return Outcome("succeeded", r.status_code, reference, redact(keep | {"replayed": bool(data.get("replayed"))}))


class ReminderSend(Adapter):
    code, label, path, proposal_kind = "reminder_send", "Mock reminder send", "/sandbox/reminders", "invoice_reminder"
    result_fields = Adapter.result_fields + ("delivered",)

    def build(self, payload: dict, extra: dict) -> dict:
        return {"reference": payload["document_number"], "customer": payload["customer"], "channel": payload["channel"],
                "amount_minor": payload["outstanding_minor"], "currency": payload["currency"], "due_date": payload["due_date"],
                "message": payload["message"], "recipient": payload["contact"]["address_masked"]}

    def redacted(self, request: dict) -> dict:
        # The message is customer-facing text: the log keeps its hash and length, not the words.
        msg = request["message"]
        return redact({k: v for k, v in request.items() if k != "message"}
                      | {"message_sha256": hashlib.sha256(msg.encode()).hexdigest(), "message_length": len(msg)})


class ApplicationSubmit(Adapter):
    code, label, path, proposal_kind = "application_submit", "Mock application submission", "/sandbox/applications", "financing_application"
    result_fields = Adapter.result_fields + ("lender_decision",)

    def build(self, payload: dict, extra: dict) -> dict:
        return {"product_id": payload["product_id"], "product": payload["product"], "provider": payload["provider"],
                "amount_minor": payload["amount_minor"], "currency": payload["currency"], "purpose": payload["purpose"],
                "needed_by": payload.get("needed_by"), "illustrative": True}


class PaymentInitiate(Adapter):
    code, label, path, proposal_kind = "payment_initiate", "Mock payment initiation", "/sandbox/payments", "supplier_payment"
    result_fields = Adapter.result_fields + ("money_moved",)

    def build(self, payload: dict, extra: dict) -> dict:
        d = extra["destination"]   # only the masked destination (institution and last four digits) is known to the app
        return {"reference": payload["document_number"], "payee": payload["supplier"],
                "destination": {"kind": d["kind"], "institution": d["institution"], "last4": d["last4"]},
                "from_account": payload["from_account"], "amount_minor": payload["amount_minor"], "currency": payload["currency"]}


class PacketShare(Adapter):
    code, label, path, proposal_kind = "packet_share", "Mock packet sharing", "/sandbox/packets", "funding_packet_share"
    result_fields = Adapter.result_fields + ("fields_received",)
    available = True
    note = "Sandbox only. Sends exactly the owner-selected packet snapshot to a simulated recipient."

    def build(self, payload: dict, extra: dict) -> dict:
        return {"packet_id": payload["packet_id"], "recipient": payload["recipient"], "fields": list(payload["disclosed_fields"]),
                "packet": payload["packet"], "packet_sha256": payload["packet_sha256"], "expires_at": payload["expires_at"]}


ADAPTERS: dict[str, Adapter] = {a.code: a for a in (ReminderSend(), ApplicationSubmit(), PaymentInitiate(), PacketShare())}
BY_KIND: dict[str, Adapter] = {a.proposal_kind: a for a in ADAPTERS.values() if a.available}

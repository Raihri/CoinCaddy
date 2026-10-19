"""Redaction applied to every audit event's details before it is stored.

- Values under sensitive keys (passwords, tokens, document/file content, raw rows) are replaced.
- Values under account-number-like keys keep only their last four digits.
- Any string containing a run of 8+ digits (optionally separated by spaces or dashes) is treated as
  a possible account/card number and masked to its last four digits. Whole UUIDs and ISO dates are
  exempt because they identify records, not accounts.
- Long strings are truncated, so document text cannot be copied into the log wholesale.
- Floats are refused: money in details must be integer minor units.
"""

import re
from typing import Any

REDACTED = "[REDACTED]"
MAX_STRING = 200
MAX_DEPTH = 5

_SENSITIVE_KEY_PARTS = ("password", "token", "secret", "cookie", "authorization", "document", "content", "file", "raw")
_ACCOUNT_KEY_PARTS = ("account_number", "account_no", "iban", "card_number", "wallet_number", "msisdn")
_DIGIT_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){7,}\d(?!\d)")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T[0-9:.]+(Z|[+-]\d{2}:\d{2})?)?$")


def mask_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return "••••" + digits[-4:] if digits else REDACTED


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else REDACTED


def _redact_string(value: str) -> str:
    if _UUID.match(value) or _ISO_DATE.match(value):
        return value
    value = _DIGIT_RUN.sub(lambda m: mask_digits(m.group(0)), value)
    return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…[truncated]"


def redact(value: Any, key: str = "", depth: int = 0) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _ACCOUNT_KEY_PARTS):
        return mask_digits(str(value)) if value is not None else None
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return REDACTED
    if depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {str(k): redact(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key, depth + 1) for v in value]
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    raise TypeError(f"Audit details may not contain {type(value).__name__} values (key {key!r})")

"""Strict request models for Business Setup. Every rule here is enforced server-side.

- Unknown fields are rejected (extra="forbid"); explicit nulls are rejected.
- Money is StrictInt minor units only: floats, decimal strings, and booleans are refused.
- Text is NFC-normalized, trimmed, whitespace-collapsed, and may not contain control characters.
"""

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

from app.contracts.money import MINOR_UNIT_EXPONENT
from app.contracts.reference import JURISDICTIONS, MAX_ACCOUNTS_PER_BUSINESS, MAX_AMOUNT_MINOR, timezones


def clean_text(value: str, max_len: int) -> str:
    value = " ".join(unicodedata.normalize("NFC", value).split())
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") for ch in value):
        raise ValueError("must not contain control or invisible formatting characters")
    if not value:
        raise ValueError("must not be empty")
    if len(value) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    return value


def check_currency(value: str) -> str:
    value = value.strip().upper()
    if value not in MINOR_UNIT_EXPONENT:
        raise ValueError(f"unsupported currency; choose one of {', '.join(sorted(MINOR_UNIT_EXPONENT))}")
    return value


def check_jurisdiction(value: str) -> str:
    value = value.strip().upper()
    if value not in JURISDICTIONS:
        raise ValueError(f"unsupported jurisdiction; choose one of {', '.join(JURISDICTIONS)}")
    return value


def check_timezone(value: str) -> str:
    value = value.strip()
    if value not in timezones():
        raise ValueError("must be an IANA timezone name such as Asia/Dhaka")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReserveIn(_Strict):
    amount_minor: StrictInt = Field(ge=0, le=MAX_AMOUNT_MINOR)
    currency: StrictStr

    validate_currency = field_validator("currency")(check_currency)


class AccountIn(_Strict):
    label: StrictStr
    kind: Literal["bank", "mobile_wallet", "cash"]
    currency: StrictStr

    validate_label = field_validator("label")(lambda v: clean_text(v, 60))
    validate_currency = field_validator("currency")(check_currency)


class AccountRelabel(_Strict):
    label: StrictStr

    validate_label = field_validator("label")(lambda v: clean_text(v, 60))


class BusinessCreate(_Strict):
    name: StrictStr
    jurisdiction: StrictStr
    base_currency: StrictStr
    timezone: StrictStr
    reserve: ReserveIn
    accounts: list[AccountIn] = Field(default_factory=list, max_length=MAX_ACCOUNTS_PER_BUSINESS)

    validate_name = field_validator("name")(lambda v: clean_text(v, 120))
    validate_jurisdiction = field_validator("jurisdiction")(check_jurisdiction)
    validate_currency = field_validator("base_currency")(check_currency)
    validate_timezone = field_validator("timezone")(check_timezone)

    @model_validator(mode="after")
    def _consistent(self) -> "BusinessCreate":
        if self.reserve.currency != self.base_currency:
            raise ValueError("reserve currency must equal the base currency")
        labels = [a.label.casefold() for a in self.accounts]
        if len(labels) != len(set(labels)):
            raise ValueError("account labels must be unique (ignoring case)")
        return self


class SettingsUpdate(_Strict):
    expected_version: StrictInt = Field(ge=1)
    name: StrictStr | None = None
    jurisdiction: StrictStr | None = None
    base_currency: StrictStr | None = None
    timezone: StrictStr | None = None
    reserve: ReserveIn | None = None

    validate_name = field_validator("name")(lambda v: clean_text(v, 120) if v is not None else v)
    validate_jurisdiction = field_validator("jurisdiction")(lambda v: check_jurisdiction(v) if v is not None else v)
    validate_currency = field_validator("base_currency")(lambda v: check_currency(v) if v is not None else v)
    validate_timezone = field_validator("timezone")(lambda v: check_timezone(v) if v is not None else v)

    @model_validator(mode="after")
    def _has_changes(self) -> "SettingsUpdate":
        fields = self.model_fields_set - {"expected_version"}
        if not fields:
            raise ValueError("provide at least one setting to change")
        nulls = sorted(f for f in fields if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"settings may not be set to null: {', '.join(nulls)}")
        return self

    def changes(self) -> dict:
        return {f: getattr(self, f) for f in self.model_fields_set - {"expected_version"}}


Kind = Literal["bank", "mobile_wallet", "cash"]

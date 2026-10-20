"""Region Packs: versioned, validated bundles of presentation settings, import templates, and an illustrative
financing catalogue for one market.

A pack changes how amounts and dates are *displayed*, which import column layouts are offered, and which
illustrative catalogue Funding Matches reads. It never changes a stored amount: money stays integer minor
units of an explicit currency, and amounts in different currencies are never combined (there is no
conversion rule anywhere in a pack). Packs are files in this repository, validated when the API starts, so
a malformed pack stops the API rather than reaching an owner. A pack is not a claim of legal compliance.
"""

import copy
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, field_validator, model_validator

from app.contracts.money import MINOR_UNIT_EXPONENT
from app.contracts.reference import JURISDICTIONS
from app.modules.funding.engine import DOCUMENTS, PURPOSES
from app.modules.records.normalize import Mapping

PACKS_DIR = Path(__file__).parent / "packs"
DEFAULT_PACK_ID = "bd"  # the catalogue used when a business's jurisdiction has no pack and none was chosen
LABEL_KEYS = ("cash", "receivables", "payables", "mobile_wallet", "reserve", "financing")
COLUMN_FIELDS = ("date", "description", "amount", "debit", "credit", "reference", "counterparty")
NO_CONVERSION_NOTE = (
    "Amounts keep the currency they were recorded in. This pack contains no exchange rate, so figures in "
    "different currencies are never added together."
)
HISTORY_NOTE = "Switching the pack changes how figures are shown and which sample catalogue is read. Saved amounts are not changed."
CATALOGUE_PRODUCT_KEYS = {
    "id", "name", "kind", "provider", "jurisdictions", "currencies", "min_amount_minor", "max_amount_minor",
    "min_months_trading", "min_monthly_revenue_minor", "purposes", "security", "documents", "funding_days",
    "pricing", "repayment", "source", "advance_rate_bps",
}
PRICING_KEYS = {
    "credit_line": {"processing_fee_bps", "interest_annual_bps", "tenor_days"},
    "invoice_advance": {"processing_fee_bps", "fee_per_30_days_bps", "tenor_days"},
    "term_loan": {"processing_fee_bps", "interest_annual_bps", "installments"},
}


class PackError(ValueError):
    """A pack file is malformed. Raised at start-up, never at request time."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _plain(value: str, max_len: int, what: str) -> str:
    value = " ".join(unicodedata.normalize("NFC", value).split())
    if not value or len(value) > max_len:
        raise ValueError(f"{what} must be 1 to {max_len} characters")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") for ch in value):
        raise ValueError(f"{what} must not contain control or invisible characters")
    return value


def header_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class NumberFormat(_Strict):
    # Amounts are typed, imported and exported with "." as the decimal separator (see records/normalize.py), so a pack may not
    # display a decimal comma that people would then be expected to type back.
    grouping: Literal["lakh", "thousands"]
    group_separator: Literal[",", " "]
    decimal_separator: Literal["."]

    @model_validator(mode="after")
    def _differ(self) -> "NumberFormat":
        if self.group_separator == self.decimal_separator:
            raise ValueError("group and decimal separators must differ")
        return self


class CurrencyDisplay(_Strict):
    symbol: StrictStr
    position: Literal["prefix", "suffix"]

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        return _plain(v, 4, "symbol")


class ImportTemplate(_Strict):
    id: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{2,39}$")
    name: StrictStr
    description: StrictStr
    date_format: Literal["YYYY-MM-DD", "YYYY/MM/DD", "DD/MM/YYYY", "MM/DD/YYYY", "DD-MM-YYYY", "DD.MM.YYYY"]
    amount_mode: Literal["signed", "debit_credit"]
    columns: dict[str, StrictStr]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _plain(v, 80, "name")

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        return _plain(v, 200, "description")

    @model_validator(mode="after")
    def _shape(self) -> "ImportTemplate":
        unknown = set(self.columns) - set(COLUMN_FIELDS)
        if unknown:
            raise ValueError(f"unknown column fields: {', '.join(sorted(unknown))}")
        names = [header_key(v) for v in self.columns.values()]
        if any(not n for n in names) or len(names) != len(set(names)):
            raise ValueError("column header names must be non-empty and different from each other")
        self.mapping_for(list(self.columns.values()))  # the same rules the importer applies to a real mapping
        return self

    def mapping_for(self, headers: list[str]) -> Mapping:
        """The importer's own Mapping for a file with these headers; ValueError when a template column is missing or repeated."""
        keys = [header_key(h) for h in headers]
        index: dict[str, int] = {}
        for field, header in self.columns.items():
            hits = [i for i, k in enumerate(keys) if k == header_key(header)]
            if len(hits) != 1:
                raise ValueError(f"column {header!r} is {'missing' if not hits else 'repeated'}")
            index[field] = hits[0]
        if "date" not in index or "description" not in index:
            raise ValueError("a template needs date and description columns")
        mapping = Mapping(
            date_column=index["date"], date_format=self.date_format, description_column=index["description"],
            amount_mode=self.amount_mode, amount_column=index.get("amount"), debit_column=index.get("debit"),
            credit_column=index.get("credit"), counterparty_column=index.get("counterparty"), reference_column=index.get("reference"),
        )
        mapping.check_against(len(headers))
        return mapping


class Pack(_Strict):
    id: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{1,19}$")
    version: StrictStr = Field(pattern=r"^region-[a-z0-9-]+-\d{4}\.\d{2}$")
    name: StrictStr
    jurisdiction: StrictStr
    currency: StrictStr
    minor_unit_exponent: StrictInt
    language: StrictStr = Field(pattern=r"^[a-z]{2}-[A-Z]{2}$")
    number_format: NumberFormat
    currency_display: CurrencyDisplay
    date_display: Literal["DD MMM YYYY", "DD/MM/YYYY", "YYYY-MM-DD"]
    labels: dict[str, StrictStr]
    import_templates: list[ImportTemplate] = Field(min_length=1, max_length=10)
    catalogue_file: StrictStr = Field(pattern=r"^[a-z0-9._-]+\.json$")
    notice: StrictStr

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _plain(v, 60, "name")

    @field_validator("notice")
    @classmethod
    def _notice(cls, v: str) -> str:
        return _plain(v, 300, "notice")

    @model_validator(mode="after")
    def _consistent(self) -> "Pack":
        if self.jurisdiction not in JURISDICTIONS:
            raise ValueError(f"jurisdiction {self.jurisdiction!r} is not a supported jurisdiction")
        if self.currency not in MINOR_UNIT_EXPONENT:
            raise ValueError(f"currency {self.currency!r} is not a supported currency")
        if self.minor_unit_exponent != MINOR_UNIT_EXPONENT[self.currency]:
            raise ValueError("minor_unit_exponent must equal the platform's exponent for the currency")
        if set(self.labels) != set(LABEL_KEYS):
            raise ValueError(f"labels must be exactly: {', '.join(LABEL_KEYS)}")
        self.labels = {k: _plain(v, 40, f"label {k}") for k, v in self.labels.items()}
        ids = [t.id for t in self.import_templates]
        if len(ids) != len(set(ids)):
            raise ValueError("import template ids must be unique")
        if not self.version.startswith(f"region-{self.id}-"):
            raise ValueError("version must start with region-<pack id>-")
        return self


def validate_catalogue(cat: dict, pack: Pack) -> dict:
    """Structural checks on a pack's illustrative catalogue: money fields are integers, terms are well formed, ids are unique."""
    for key in ("version", "label", "updated", "source"):
        if not isinstance(cat.get(key), str) or not cat[key]:
            raise PackError(f"catalogue of {pack.id}: missing {key!r}")
    if not isinstance(cat.get("products"), list) or not cat["products"]:
        raise PackError(f"catalogue of {pack.id}: no products")
    seen: set[str] = set()
    market_products = 0
    for p in cat["products"]:
        pid = p.get("id", "?")
        where = f"catalogue of {pack.id}, product {pid}"
        if set(p) - CATALOGUE_PRODUCT_KEYS:
            raise PackError(f"{where}: unknown fields {sorted(set(p) - CATALOGUE_PRODUCT_KEYS)}")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,39}", pid) or pid in seen:
            raise PackError(f"{where}: id must be unique lowercase letters, digits and hyphens")
        seen.add(pid)
        if p.get("kind") not in PRICING_KEYS:
            raise PackError(f"{where}: unknown kind {p.get('kind')!r}")
        if set(p.get("pricing", {})) != PRICING_KEYS[p["kind"]]:
            raise PackError(f"{where}: pricing must have exactly {sorted(PRICING_KEYS[p['kind']])}")
        ints = [p.get(k) for k in ("min_amount_minor", "max_amount_minor", "min_months_trading", "min_monthly_revenue_minor", "funding_days")]
        ints += list(p["pricing"].values())
        if any(type(v) is not int or v < 0 for v in ints):
            raise PackError(f"{where}: amounts, months, days and basis points must be non-negative integers (no decimals)")
        if not 0 < p["min_amount_minor"] <= p["max_amount_minor"]:
            raise PackError(f"{where}: amount range is empty")
        codes = (p.get("jurisdictions") or []) + (p.get("currencies") or [])
        if not p.get("jurisdictions") or not p.get("currencies") or any(not re.fullmatch(r"[A-Z]{2,3}", c) for c in codes):
            raise PackError(f"{where}: jurisdictions and currencies must be listed as codes")
        if not p.get("purposes") or not set(p["purposes"]) <= set(PURPOSES) or not set(p.get("documents", [])) <= set(DOCUMENTS):
            raise PackError(f"{where}: unknown purpose or document")
        if p.get("security") not in ("none", "receivables", "guarantor", "collateral"):
            raise PackError(f"{where}: unknown security")
        if p["security"] == "receivables" and type(p.get("advance_rate_bps")) is not int:
            raise PackError(f"{where}: receivables security needs an integer advance_rate_bps")
        if not isinstance(p.get("source"), dict) or not p["source"].get("label") or not p["source"].get("updated"):
            raise PackError(f"{where}: every product needs a source label and date")
        if pack.jurisdiction in p["jurisdictions"] and pack.currency in p["currencies"]:
            market_products += 1
    if market_products == 0:
        raise PackError(f"catalogue of {pack.id}: no product is offered in {pack.jurisdiction} in {pack.currency}")
    return cat


class LoadedPack:
    def __init__(self, pack: Pack, catalogue: dict):
        self.pack = pack
        self.catalogue = catalogue

    def summary(self) -> dict:
        p = self.pack
        return {"id": p.id, "name": p.name, "version": p.version, "jurisdiction": p.jurisdiction,
                "jurisdiction_name": JURISDICTIONS[p.jurisdiction][0], "currency": p.currency, "language": p.language}

    def detail(self) -> dict:
        p, c = self.pack, self.catalogue
        return self.summary() | {
            "minor_unit_exponent": p.minor_unit_exponent,
            "number_format": p.number_format.model_dump(), "currency_display": p.currency_display.model_dump(),
            "date_display": p.date_display, "labels": p.labels, "notice": p.notice,
            "import_templates": [
                {"id": t.id, "name": t.name, "description": t.description, "date_format": t.date_format,
                 "amount_mode": t.amount_mode, "columns": t.columns}
                for t in p.import_templates
            ],
            "catalogue": {
                "version": c["version"], "label": c["label"], "updated": c["updated"], "source": c["source"],
                "products": [{"id": x["id"], "name": x["name"], "kind": x["kind"], "provider": x["provider"],
                              "jurisdictions": x["jurisdictions"], "currencies": x["currencies"]} for x in c["products"]],
            },
            "no_conversion_note": NO_CONVERSION_NOTE, "history_note": HISTORY_NOTE,
        }

    def stamp(self) -> dict:
        return {"id": self.pack.id, "version": self.pack.version, "jurisdiction": self.pack.jurisdiction, "currency": self.pack.currency}

    def catalogue_for_run(self) -> dict:
        """A private copy of the catalogue, stamped with the pack, so a saved Funding Match records which pack it read."""
        return copy.deepcopy(self.catalogue) | {"region_pack": self.stamp()}


@lru_cache
def load_packs() -> dict[str, LoadedPack]:
    packs: dict[str, LoadedPack] = {}
    for folder in sorted(p for p in PACKS_DIR.iterdir() if p.is_dir()):
        try:
            pack = Pack.model_validate(json.loads((folder / "pack.json").read_text(encoding="utf-8")))
            if pack.id != folder.name:
                raise PackError(f"pack id {pack.id!r} must match its folder name {folder.name!r}")
            catalogue = validate_catalogue(json.loads((folder / pack.catalogue_file).read_text(encoding="utf-8")), pack)
        except (ValidationError, OSError, json.JSONDecodeError) as exc:
            raise PackError(f"region pack {folder.name!r} is invalid: {exc}") from exc
        packs[pack.id] = LoadedPack(pack, catalogue)
    jurisdictions = [lp.pack.jurisdiction for lp in packs.values()]
    if len(jurisdictions) != len(set(jurisdictions)):
        raise PackError("two packs claim the same jurisdiction")
    if DEFAULT_PACK_ID not in packs:
        raise PackError(f"the default pack {DEFAULT_PACK_ID!r} is missing")
    return packs


def get_pack(pack_id: str) -> LoadedPack | None:
    return load_packs().get(pack_id)


def pack_for_jurisdiction(jurisdiction: str) -> LoadedPack | None:
    return next((lp for lp in load_packs().values() if lp.pack.jurisdiction == jurisdiction), None)


def default_catalogue() -> dict:
    return load_packs()[DEFAULT_PACK_ID].catalogue

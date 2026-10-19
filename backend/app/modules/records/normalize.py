"""Turn one staged row into canonical fields, or a list of row errors. Pure functions, no I/O.

Amounts use '.' as the decimal separator. Commas are accepted only as valid thousands (1,234,567) or
lakh (12,34,567) grouping, so '1.234,50' and '12,5' are rejected as ambiguous instead of guessed.
Dates use the explicit format chosen in the mapping; two-digit years are never accepted.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, model_validator

from app.contracts.money import MINOR_UNIT_EXPONENT

DATE_FORMATS: dict[str, re.Pattern] = {
    "YYYY-MM-DD": re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$"),
    "YYYY/MM/DD": re.compile(r"^(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})$"),
    "DD/MM/YYYY": re.compile(r"^(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$"),
    "MM/DD/YYYY": re.compile(r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})$"),
    "DD-MM-YYYY": re.compile(r"^(?P<d>\d{1,2})-(?P<m>\d{1,2})-(?P<y>\d{4})$"),
    "DD.MM.YYYY": re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$"),
}
DateFormat = Literal["YYYY-MM-DD", "YYYY/MM/DD", "DD/MM/YYYY", "MM/DD/YYYY", "DD-MM-YYYY", "DD.MM.YYYY"]

EARLIEST_DATE = date(2000, 1, 1)
MAX_ABS_MINOR = 10**15
MAX_DESCRIPTION = 200
MAX_SHORT_TEXT = 120

CURRENCY_SYMBOLS = {"BDT": ("৳", "Tk", "TK"), "USD": ("$", "US$"), "EUR": ("€",), "INR": ("₹", "Rs"), "JPY": ("¥", "￥")}
_NUMBER = re.compile(r"^(?P<int>\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3}|\d+)(?:\.(?P<frac>\d+))?$")


class Mapping(BaseModel):
    """Column indexes refer to the header row. Validated again against the batch's headers."""

    model_config = ConfigDict(extra="forbid")

    date_column: StrictInt
    date_format: DateFormat
    description_column: StrictInt
    amount_mode: Literal["signed", "debit_credit"]
    amount_column: StrictInt | None = None
    debit_column: StrictInt | None = None
    credit_column: StrictInt | None = None
    counterparty_column: StrictInt | None = None
    reference_column: StrictInt | None = None
    currency_column: StrictInt | None = None

    @model_validator(mode="after")
    def _shape(self) -> "Mapping":
        if self.amount_mode == "signed":
            if self.amount_column is None or self.debit_column is not None or self.credit_column is not None:
                raise ValueError("signed mode needs amount_column and no debit/credit columns")
        elif self.debit_column is None or self.credit_column is None or self.amount_column is not None:
            raise ValueError("debit_credit mode needs debit_column and credit_column and no amount_column")
        used = [c for c in self.columns() if c is not None]
        if len(used) != len(set(used)):
            raise ValueError("each column can be mapped to only one field")
        return self

    def columns(self) -> list[int | None]:
        return [self.date_column, self.description_column, self.amount_column, self.debit_column,
                self.credit_column, self.counterparty_column, self.reference_column, self.currency_column]

    def check_against(self, width: int) -> None:
        bad = [c for c in self.columns() if c is not None and not 0 <= c < width]
        if bad:
            raise ValueError(f"column index out of range: {bad[0]} (file has {width} columns)")


@dataclass
class RowResult:
    errors: list[dict] = field(default_factory=list)
    business_date: date | None = None
    amount_minor: int | None = None
    currency: str | None = None
    description: str | None = None
    counterparty: str | None = None
    reference: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def err(self, field_name: str, message: str) -> None:
        self.errors.append({"field": field_name, "message": message})


def _cell(raw: list, idx: int | None) -> dict | None:
    if idx is None or idx >= len(raw):
        return None
    return raw[idx]


def looks_like_formula(text: str) -> bool:
    s = text.lstrip(" ")
    if not s:
        return False
    if s[0] in "=@\t\r":
        return True
    # A leading + or - is only acceptable as the sign of a plain number.
    return s[0] in "+-" and not re.fullmatch(r"[+-]\s*[\d.,]+", s.strip())


def _clean_text(text: str) -> str | None:
    text = " ".join(unicodedata.normalize("NFC", text).split())
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") for ch in text):
        return None
    return text


def parse_text_date(text: str, fmt: str) -> date | None:
    m = DATE_FORMATS[fmt].match(text.strip())
    if not m:
        return None
    try:
        return date(int(m["y"]), int(m["m"]), int(m["d"]))
    except ValueError:
        return None


def date_format_candidates(raw_rows: list[list], column: int) -> list[str]:
    """Formats that parse the most text dates in the column (a few bad rows do not hide the right
    format). More than one candidate means the file is ambiguous and the user must choose."""
    texts = [c["v"] for r in raw_rows if (c := _cell(r, column)) and c["t"] == "text"]
    if not texts:
        return list(DATE_FORMATS)
    scores = {f: sum(parse_text_date(t, f) is not None for t in texts) for f in DATE_FORMATS}
    best = max(scores.values())
    return [f for f, s in scores.items() if s == best] if best else []


def parse_amount(cell: dict, currency: str, *, unsigned: bool) -> tuple[int | None, str | None]:
    exp = MINOR_UNIT_EXPONENT[currency]
    if cell["t"] == "num":
        try:
            d = Decimal(cell["v"])
        except InvalidOperation:
            return None, "is not a number"
    elif cell["t"] == "text":
        s = cell["v"].strip()
        parenthesized = s.startswith("(") and s.endswith(")")
        if parenthesized:  # accounting style: (1,234.50) means negative
            s = s[1:-1].strip()
        sign = ""
        if s[:1] in "+-":
            sign, s = s[0], s[1:].strip()
        for sym in sorted((currency, *CURRENCY_SYMBOLS.get(currency, ())), key=len, reverse=True):
            if s.startswith(sym):
                s = s[len(sym):].strip()
                break
            if s.endswith(sym):
                s = s[: -len(sym)].strip()
                break
        if parenthesized and sign:
            return None, "has an ambiguous sign (both parentheses and +/-)"
        m = _NUMBER.match(s)
        if not m:
            return None, "is not in a recognised format (use 1,234.50 with '.' for decimals)"
        d = Decimal(m["int"].replace(",", "") + ("." + m["frac"] if m["frac"] else ""))
        if parenthesized or sign == "-":
            d = -d
    else:
        return None, "must be a number, not a date"
    if not d.is_finite():
        return None, "is not a finite number"
    if abs(d) > Decimal(MAX_ABS_MINOR).scaleb(-exp):
        return None, "is too large"
    places = max(0, -d.normalize().as_tuple().exponent)
    if places > exp:
        return None, f"has more than {exp} decimal places for {currency}" if exp else f"cannot have decimals for {currency}"
    minor = int(d.scaleb(exp))
    if unsigned and minor < 0:
        return None, "must not be negative in a debit/credit column"
    if abs(minor) > MAX_ABS_MINOR:
        return None, "is too large"
    return minor, None


def normalize_row(raw: list, mapping: Mapping, account_currency: str, today: date) -> RowResult:
    res = RowResult(currency=account_currency)

    for idx, cell in enumerate(raw):
        if cell and cell["t"] == "text":
            if len(cell["v"]) > 500:
                res.err(f"column {idx + 1}", "is longer than 500 characters")
            elif looks_like_formula(cell["v"]):
                res.err(f"column {idx + 1}", "looks like a spreadsheet formula; formulas are not accepted")
    if res.errors:
        return res

    # Date
    c = _cell(raw, mapping.date_column)
    if c is None:
        res.err("date", "is missing")
    else:
        d = date.fromisoformat(c["v"]) if c["t"] == "date" else parse_text_date(c["v"], mapping.date_format) if c["t"] == "text" else None
        if d is None:
            res.err("date", f"'{c['v']}' does not match {mapping.date_format}")
        elif d < EARLIEST_DATE:
            res.err("date", "is before 2000-01-01")
        elif d > today:
            res.err("date", "is in the future for this business's timezone")
        else:
            res.business_date = d

    # Currency
    c = _cell(raw, mapping.currency_column)
    if c is not None:
        code = c["v"].strip().upper() if c["t"] == "text" else ""
        if code != account_currency:
            res.err("currency", f"'{c['v']}' differs from the account currency {account_currency}; no conversion is applied")

    # Amount
    if mapping.amount_mode == "signed":
        c = _cell(raw, mapping.amount_column)
        if c is None:
            res.err("amount", "is missing")
        else:
            res.amount_minor, problem = parse_amount(c, account_currency, unsigned=False)
            if problem:
                res.err("amount", problem)
    else:
        debit, credit = _cell(raw, mapping.debit_column), _cell(raw, mapping.credit_column)
        if (debit is None) == (credit is None):
            res.err("amount", "exactly one of debit or credit must be filled")
        else:
            value, problem = parse_amount(debit or credit, account_currency, unsigned=True)
            if problem:
                res.err("debit" if debit else "credit", problem)
            else:
                res.amount_minor = -value if debit else value
    if res.amount_minor == 0:
        res.err("amount", "is zero")
        res.amount_minor = None

    # Text fields
    c = _cell(raw, mapping.description_column)
    text = _clean_text(c["v"]) if c and c["t"] == "text" else (c["v"] if c else None)
    if not text:
        res.err("description", "is missing or contains invisible control characters")
    elif len(text) > MAX_DESCRIPTION:
        res.err("description", f"is longer than {MAX_DESCRIPTION} characters")
    else:
        res.description = text
    for name, idx in (("counterparty", mapping.counterparty_column), ("reference", mapping.reference_column)):
        c = _cell(raw, idx)
        if c is None:
            continue
        value = _clean_text(str(c["v"]))
        if value is None:
            res.err(name, "contains invisible control characters")
        elif len(value) > MAX_SHORT_TEXT:
            res.err(name, f"is longer than {MAX_SHORT_TEXT} characters")
        else:
            setattr(res, name, value or None)

    if res.errors:
        res.business_date = res.amount_minor = res.description = res.counterparty = res.reference = None
    return res


def match_key(description: str | None) -> str:
    return " ".join((description or "").casefold().split())


def fingerprint(account_id: str, r: RowResult) -> str:
    parts = [account_id, r.business_date.isoformat(), str(r.amount_minor), match_key(r.description), (r.reference or "").casefold()]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()

"""Money data contract.

Every monetary amount is a signed integer count of the currency's minor unit, paired with an
explicit ISO 4217 code. Floats are rejected, and amounts in different currencies never combine.
"""

from __future__ import annotations

from dataclasses import dataclass

# Minor-unit exponents for currencies the prototype accepts. Extend deliberately, not implicitly.
MINOR_UNIT_EXPONENT: dict[str, int] = {
    "BDT": 2,
    "USD": 2,
    "EUR": 2,
    "INR": 2,
    "JPY": 0,
}


class CurrencyError(ValueError):
    pass


def require_currency(code: str) -> str:
    if code not in MINOR_UNIT_EXPONENT:
        raise CurrencyError(f"Unsupported currency code: {code!r}")
    return code


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        # bool is a subclass of int; reject it as well as floats/decimals.
        if type(self.amount_minor) is not int:
            raise TypeError("amount_minor must be an int count of minor units")
        require_currency(self.currency)

    def _same_currency(self, other: Money) -> None:
        if other.currency != self.currency:
            raise CurrencyError(
                f"Cannot combine {self.currency} and {other.currency} without an explicit conversion rule"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def to_json(self) -> dict[str, object]:
        return {"amount_minor": self.amount_minor, "currency": self.currency}

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Money:
        return cls(data["amount_minor"], data["currency"])  # type: ignore[arg-type]

    def format_major(self) -> str:
        """Plain decimal string for display/debugging, e.g. 'BDT 162249.75'. Not for arithmetic."""
        exp = MINOR_UNIT_EXPONENT[self.currency]
        sign = "-" if self.amount_minor < 0 else ""
        whole, frac = divmod(abs(self.amount_minor), 10**exp)
        return f"{self.currency} {sign}{whole}" + (f".{frac:0{exp}d}" if exp else "")

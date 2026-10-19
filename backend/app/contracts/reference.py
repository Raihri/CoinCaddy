"""Reference data the server validates business settings against.

Jurisdictions are an explicit supported list, not every ISO country: adding one is a deliberate
product decision (see the later Region Pack prompt). Listing a jurisdiction is not a claim of legal
or regulatory compliance there.
"""

from functools import lru_cache
from zoneinfo import available_timezones

from app.contracts.money import MINOR_UNIT_EXPONENT

# code -> (display name, suggested currency, suggested timezone)
JURISDICTIONS: dict[str, tuple[str, str, str]] = {
    "BD": ("Bangladesh", "BDT", "Asia/Dhaka"),
    "IN": ("India", "INR", "Asia/Kolkata"),
    "US": ("United States", "USD", "America/New_York"),
    "JP": ("Japan", "JPY", "Asia/Tokyo"),
    "DE": ("Germany", "EUR", "Europe/Berlin"),
}

ACCOUNT_KINDS = ("bank", "mobile_wallet", "cash")

# Keeps reserve values exact in JavaScript clients (Number.MAX_SAFE_INTEGER is about 9.007e15).
MAX_AMOUNT_MINOR = 10**15
MAX_ACCOUNTS_PER_BUSINESS = 20


@lru_cache
def timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def options() -> dict:
    return {
        "jurisdictions": [
            {"code": c, "name": n, "suggested_currency": cur, "suggested_timezone": tz}
            for c, (n, cur, tz) in JURISDICTIONS.items()
        ],
        "currencies": [{"code": c, "minor_unit_exponent": e} for c, e in sorted(MINOR_UNIT_EXPONENT.items())],
        "timezones": sorted(timezones()),
        "account_kinds": list(ACCOUNT_KINDS),
        "limits": {"max_amount_minor": MAX_AMOUNT_MINOR, "max_accounts": MAX_ACCOUNTS_PER_BUSINESS,
                   "name_max": 120, "label_max": 60},
    }

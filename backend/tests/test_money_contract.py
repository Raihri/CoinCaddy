from datetime import date, datetime, timezone

import pytest

from app.contracts.business_time import TimezoneError, business_date, require_timezone
from app.contracts.money import CurrencyError, Money


def test_money_requires_integer_minor_units():
    with pytest.raises(TypeError):
        Money(100.5, "BDT")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(True, "BDT")  # type: ignore[arg-type]


def test_money_rejects_unknown_currency():
    with pytest.raises(CurrencyError):
        Money(100, "XYZ")


def test_money_never_combines_currencies():
    with pytest.raises(CurrencyError):
        Money(100, "BDT") + Money(100, "USD")


def test_money_arithmetic_is_exact():
    assert Money(16224975, "BDT") + Money(6300035, "BDT") == Money(22525010, "BDT")
    assert Money(-575040, "BDT").format_major() == "BDT -5750.40"
    assert Money(500, "JPY").format_major() == "JPY 500"


def test_business_date_uses_business_timezone():
    # 18:30 UTC on 9 Oct is 00:30 on 10 Oct in Dhaka (UTC+6).
    event = datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)
    assert business_date(event, "Asia/Dhaka") == date(2026, 10, 10)
    assert business_date(event, "UTC") == date(2026, 10, 9)


def test_business_date_rejects_naive_timestamp_and_unknown_zone():
    with pytest.raises(TimezoneError):
        business_date(datetime(2026, 10, 9, 18, 30), "Asia/Dhaka")
    with pytest.raises(TimezoneError):
        require_timezone("Mars/Olympus")

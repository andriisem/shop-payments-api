"""Money rules (NFR-1): amounts are Decimal in whole cents; the provider gets minor units."""

import re
from decimal import Decimal

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1E10")  # NUMERIC(12,2) holds at most 9,999,999,999.99
CURRENCY_CODE = re.compile(r"[A-Z]{3}")

# ISO 4217 currencies without exactly 2 decimals. Minor units assume 2, so 70 JPY would be
# charged as 7000 (100 times too much) and 70 KWD as 7000 (a tenth of the price).
# fmt: off
ZERO_DECIMAL_CURRENCIES = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG",
    "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
})
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})
# fmt: on


def is_chargeable_amount(amount: Decimal) -> bool:
    """EC-9: finite, positive, in whole cents, and small enough for NUMERIC(12,2)."""
    return amount.is_finite() and 0 < amount < MAX_AMOUNT and amount == amount.quantize(CENT)


def is_supported_currency(currency: str) -> bool:
    """EC-11: three upper-case letters, for a currency with exactly 2 decimals."""
    return (
        CURRENCY_CODE.fullmatch(currency) is not None
        and currency not in ZERO_DECIMAL_CURRENCIES
        and currency not in THREE_DECIMAL_CURRENCIES
    )


def to_cents(amount: Decimal) -> Decimal:
    """Decimal("70") -> Decimal("70.00"), so every stored amount has 2 decimal places."""
    return amount.quantize(CENT)


def to_minor_units(amount: Decimal) -> int:
    """Decimal("70.00") -> 7000. Valid for 2-decimal currencies only."""
    minor = amount * 100
    # The service only passes amounts that is_chargeable_amount() accepted, so this cannot
    # fail there. It stays as a backstop: int() would silently drop a fraction of a cent.
    if minor != minor.to_integral_value():
        raise ValueError(f"Amount {amount} has more than 2 decimal places")
    return int(minor)

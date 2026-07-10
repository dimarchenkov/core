from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

DEFAULT_CURRENCY = "RUB"
MONEY_QUANT = Decimal("0.01")
MONEY_ROUNDING = ROUND_HALF_UP


def quantize_money(amount: Decimal) -> Decimal:
    """Return a money amount rounded to two decimal places for storage."""
    if not isinstance(amount, Decimal):
        msg = "Money amounts must be Decimal values."
        raise TypeError(msg)

    return amount.quantize(MONEY_QUANT, rounding=MONEY_ROUNDING)

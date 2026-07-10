from __future__ import annotations

from decimal import Decimal

import pytest

from core.shared.money import DEFAULT_CURRENCY, quantize_money


def test_default_currency_is_rub() -> None:
    assert DEFAULT_CURRENCY == "RUB"


def test_quantize_money_rounds_half_up() -> None:
    assert quantize_money(Decimal("10.125")) == Decimal("10.13")


def test_quantize_money_rounds_down_below_half_cent() -> None:
    assert quantize_money(Decimal("10.124")) == Decimal("10.12")


def test_quantize_money_converts_199_995_to_200_00() -> None:
    assert quantize_money(Decimal("199.995")) == Decimal("200.00")


def test_quantize_money_rejects_float_input() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        quantize_money(199.995)  # type: ignore[arg-type]

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.rental.order_enums import RentalOverdueState
from core.rental.return_calculator import RentalReturnRuleError, calculate_return

DUE = datetime(2026, 10, 11, 14, tzinfo=UTC)
SNAPSHOT: dict[str, str | int | bool | None] = {
    "tariff_type": "duration",
    "tariff_name": "3 часа",
    "duration_minutes": 180,
    "grace_minutes": 30,
    "agreed_price": "300.00",
    "overdue_policy": "next_period",
    "overdue_unit_minutes": 180,
    "overdue_unit_price": "300.00",
}


@pytest.mark.parametrize(
    ("offset", "state", "minutes", "blocks", "charge"),
    [
        (timedelta(minutes=-5), RentalOverdueState.ON_TIME, 0, 0, "0.00"),
        (timedelta(minutes=20), RentalOverdueState.OVERDUE_GRACE, 20, 0, "0.00"),
        (timedelta(minutes=30), RentalOverdueState.OVERDUE_GRACE, 30, 0, "0.00"),
        (
            timedelta(minutes=30, seconds=1),
            RentalOverdueState.OVERDUE_CHARGEABLE,
            31,
            1,
            "300.00",
        ),
        (
            timedelta(minutes=179),
            RentalOverdueState.OVERDUE_CHARGEABLE,
            179,
            1,
            "300.00",
        ),
        (
            timedelta(minutes=210),
            RentalOverdueState.OVERDUE_CHARGEABLE,
            210,
            1,
            "300.00",
        ),
        (
            timedelta(minutes=210, seconds=1),
            RentalOverdueState.OVERDUE_CHARGEABLE,
            211,
            2,
            "600.00",
        ),
        (
            timedelta(minutes=390),
            RentalOverdueState.OVERDUE_CHARGEABLE,
            390,
            2,
            "600.00",
        ),
    ],
)
def test_fixed_period_overdue_boundaries(
    offset: timedelta,
    state: RentalOverdueState,
    minutes: int,
    blocks: int,
    charge: str,
) -> None:
    """Grace is free once; later time is billed in full fixed tariff periods."""
    result = calculate_return(
        tariff_snapshot=SNAPSHOT,
        agreed_price=Decimal("300"),
        discount=Decimal("0"),
        due_at=DUE,
        returned_at=DUE + offset,
    )

    assert result.overdue_state is state
    assert result.overdue_minutes == minutes
    assert result.overdue_blocks == blocks
    assert result.overdue_charge == Decimal(charge)
    assert result.base_rental_price == Decimal("300.00")


def test_fixed_tariff_is_not_prorated_and_damage_is_separate() -> None:
    """Early return keeps the full base price and manual damage remains auditable."""
    result = calculate_return(
        tariff_snapshot=SNAPSHOT,
        agreed_price=Decimal("300"),
        discount=Decimal("0"),
        due_at=DUE,
        returned_at=DUE - timedelta(hours=1),
        damage_charge=Decimal("125.50"),
    )

    assert result.base_rental_price == Decimal("300.00")
    assert result.overdue_charge == Decimal("0.00")
    assert result.damage_charge == Decimal("125.50")
    assert result.total_charge == Decimal("425.50")


def test_slot_uses_frozen_slot_length_as_next_billing_period() -> None:
    """SLOT billing starts from its fixed due boundary and never shifts with issue."""
    snapshot = {
        **SNAPSHOT,
        "tariff_type": "slot",
        "duration_minutes": None,
        "slot_start": "09:00",
        "slot_end": "14:00",
        "overdue_unit_minutes": None,
    }
    result = calculate_return(
        tariff_snapshot=snapshot,
        agreed_price=Decimal("300"),
        discount=Decimal("0"),
        due_at=DUE,
        returned_at=DUE + timedelta(minutes=331),
    )

    assert result.rule.unit_minutes == 300
    assert result.overdue_blocks == 2
    assert result.overdue_charge == Decimal("600.00")


def test_unknown_snapshot_period_fails_instead_of_guessing() -> None:
    """Historical charging refuses an incomplete rule rather than reading live tariffs."""
    with pytest.raises(RentalReturnRuleError):
        calculate_return(
            tariff_snapshot={"grace_minutes": 30, "agreed_price": "300.00"},
            agreed_price=Decimal("300"),
            discount=Decimal("0"),
            due_at=DUE,
            returned_at=DUE + timedelta(hours=1),
        )

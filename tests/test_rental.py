from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from core.rental import (
    AssetCondition,
    AssetPurpose,
    InvalidAssetConditionError,
    InvalidAssetNumberError,
    InvalidRentalStateError,
    RentalAsset,
    RentalAvailability,
    RetirementReasonRequiredError,
)

ASSET_ID = UUID("019c0000-0000-7000-8000-000000000001")
VARIANT_ID = UUID("019c0000-0000-7000-8000-000000000002")
INTAKE_ITEM_ID = UUID("019c0000-0000-7000-8000-000000000003")
CREATED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)


def create_asset(condition: AssetCondition = AssetCondition.NEW) -> RentalAsset:
    """Create one deterministic aggregate for a focused domain test."""
    return RentalAsset.create(
        asset_id=ASSET_ID,
        asset_number=" RENT-000001 ",
        variant_id=VARIANT_ID,
        intake_item_id=INTAKE_ITEM_ID,
        condition=condition,
        created_at=CREATED_AT,
    )


@pytest.mark.parametrize(
    "condition",
    [AssetCondition.NEW, AssetCondition.GOOD, AssetCondition.FAIR],
)
def test_create_serviceable_asset_makes_it_available(condition: AssetCondition) -> None:
    """A serviceable intake unit enters the available rental pool."""
    asset = create_asset(condition)

    assert asset.asset_number == "RENT-000001"
    assert asset.purpose is AssetPurpose.RENTAL
    assert asset.condition is condition
    assert asset.availability is RentalAvailability.AVAILABLE
    assert asset.created_at == CREATED_AT
    assert asset.updated_at == CREATED_AT


@pytest.mark.parametrize(
    "condition",
    [AssetCondition.DAMAGED, AssetCondition.UNUSABLE],
)
def test_create_unserviceable_asset_routes_it_to_maintenance(
    condition: AssetCondition,
) -> None:
    """A defective intake unit retains identity without becoming rentable."""
    asset = create_asset(condition)

    assert asset.condition is condition
    assert asset.availability is RentalAvailability.MAINTENANCE


def test_create_requires_asset_number() -> None:
    """A physical item cannot enter tracking without its business identifier."""
    with pytest.raises(InvalidAssetNumberError):
        RentalAsset.create(
            asset_id=ASSET_ID,
            asset_number="  ",
            variant_id=VARIANT_ID,
            intake_item_id=INTAKE_ITEM_ID,
            condition=AssetCondition.NEW,
        )


def test_create_rejects_condition_outside_domain_enum() -> None:
    """Runtime callers cannot bypass the declared condition vocabulary."""
    with pytest.raises(InvalidAssetConditionError):
        RentalAsset.create(
            asset_id=ASSET_ID,
            asset_number="RENT-000001",
            variant_id=VARIANT_ID,
            intake_item_id=INTAKE_ITEM_ID,
            condition="new",  # type: ignore[arg-type]
        )


def test_public_state_is_read_only() -> None:
    """Callers cannot replace aggregate state without a domain command."""
    asset = create_asset()

    with pytest.raises(AttributeError):
        asset.purpose = AssetPurpose.SALE  # type: ignore[misc]
    with pytest.raises(AttributeError):
        asset.condition = AssetCondition.FAIR  # type: ignore[misc]
    with pytest.raises(AttributeError):
        asset.availability = RentalAvailability.RENTED  # type: ignore[misc]


def test_checkout_and_serviceable_return_complete_normal_cycle() -> None:
    """A healthy asset can be issued and returned to the available pool."""
    asset = create_asset()

    asset.checkout()
    assert asset.availability is RentalAvailability.RENTED

    asset.accept_return(AssetCondition.GOOD)
    assert asset.condition is AssetCondition.GOOD
    assert asset.availability is RentalAvailability.AVAILABLE


@pytest.mark.parametrize(
    "condition",
    [AssetCondition.DAMAGED, AssetCondition.UNUSABLE],
)
def test_damaged_return_routes_asset_to_maintenance(condition: AssetCondition) -> None:
    """Return inspection cannot hide a defect by making the asset available."""
    asset = create_asset()
    asset.checkout()

    asset.accept_return(condition)

    assert asset.condition is condition
    assert asset.availability is RentalAvailability.MAINTENANCE


def test_checkout_rejects_asset_that_is_not_available() -> None:
    """The same physical item cannot be issued twice."""
    asset = create_asset()
    asset.checkout()

    with pytest.raises(InvalidRentalStateError):
        asset.checkout()


def test_accept_return_requires_rented_asset() -> None:
    """A return cannot be recorded without a preceding checkout."""
    asset = create_asset()

    with pytest.raises(InvalidRentalStateError):
        asset.accept_return(AssetCondition.GOOD)


def test_maintenance_cycle_restores_serviceable_asset() -> None:
    """Successful maintenance returns a healthy rental item to availability."""
    asset = create_asset(AssetCondition.FAIR)

    asset.send_to_maintenance()
    assert asset.availability is RentalAvailability.MAINTENANCE

    asset.complete_maintenance(AssetCondition.GOOD)
    assert asset.condition is AssetCondition.GOOD
    assert asset.availability is RentalAvailability.AVAILABLE


def test_failed_maintenance_keeps_asset_unavailable() -> None:
    """An unsuccessful repair cannot return a damaged item to circulation."""
    asset = create_asset(AssetCondition.DAMAGED)

    asset.complete_maintenance(AssetCondition.UNUSABLE)

    assert asset.condition is AssetCondition.UNUSABLE
    assert asset.availability is RentalAvailability.MAINTENANCE


def test_rented_asset_cannot_enter_maintenance() -> None:
    """Maintenance cannot start before an issued item has been returned."""
    asset = create_asset()
    asset.checkout()

    with pytest.raises(InvalidRentalStateError):
        asset.send_to_maintenance()


def test_withdraw_for_sale_preserves_identity_and_history_fields() -> None:
    """Withdrawal changes purpose without replacing the tracked item."""
    asset = create_asset(AssetCondition.FAIR)

    asset.withdraw_for_sale()

    assert asset.id == ASSET_ID
    assert asset.intake_item_id == INTAKE_ITEM_ID
    assert asset.purpose is AssetPurpose.SALE
    assert asset.condition is AssetCondition.FAIR


def test_unusable_asset_cannot_be_withdrawn_for_sale() -> None:
    """Sprint 9 explicitly excludes unusable items from sale withdrawal."""
    asset = RentalAsset(
        ASSET_ID,
        "RENT-000001",
        VARIANT_ID,
        INTAKE_ITEM_ID,
        AssetPurpose.RENTAL,
        AssetCondition.UNUSABLE,
        RentalAvailability.AVAILABLE,
        CREATED_AT,
        CREATED_AT,
    )

    with pytest.raises(InvalidRentalStateError):
        asset.withdraw_for_sale()


def test_rented_asset_cannot_be_withdrawn_for_sale() -> None:
    """An issued item must return before its business purpose can change."""
    asset = create_asset()
    asset.checkout()

    with pytest.raises(InvalidRentalStateError):
        asset.withdraw_for_sale()


def test_retire_requires_meaningful_reason() -> None:
    """Terminal removal must retain its business explanation."""
    asset = create_asset()

    with pytest.raises(RetirementReasonRequiredError):
        asset.retire("  ")


def test_retire_is_terminal_and_preserves_reason() -> None:
    """A retired asset cannot re-enter ordinary rental lifecycle commands."""
    asset = create_asset()

    asset.retire("Экономически нецелесообразный ремонт")

    assert asset.purpose is AssetPurpose.RETIRED
    assert asset.retirement_reason == "Экономически нецелесообразный ремонт"
    with pytest.raises(InvalidRentalStateError):
        asset.checkout()
    with pytest.raises(InvalidRentalStateError):
        asset.retire("Повторное списание")


def test_rented_asset_cannot_be_retired() -> None:
    """An issued physical item must be returned before terminal removal."""
    asset = create_asset()
    asset.checkout()

    with pytest.raises(InvalidRentalStateError):
        asset.retire("Утрата")

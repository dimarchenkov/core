from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.exceptions import (
    InvalidAssetConditionError,
    InvalidAssetNumberError,
    InvalidRentalStateError,
    RetirementReasonRequiredError,
)

_SERVICEABLE_CONDITIONS = frozenset(
    {
        AssetCondition.NEW,
        AssetCondition.GOOD,
        AssetCondition.FAIR,
    }
)
@dataclass(slots=True, init=False)
class RentalAsset:
    """One individually tracked physical item in the rental lifecycle.

    The aggregate owns all transitions of purpose, physical condition, and
    operational availability. Callers cannot use its commands to bypass the
    invariants documented for checkout, return, maintenance, sale withdrawal,
    or retirement.
    """

    _id: UUID
    _asset_number: str
    _variant_id: UUID
    _intake_item_id: UUID
    _purpose: AssetPurpose
    _condition: AssetCondition
    _availability: RentalAvailability
    _created_at: datetime
    _updated_at: datetime
    _retirement_reason: str | None

    def __init__(
        self,
        id: UUID,
        asset_number: str,
        variant_id: UUID,
        intake_item_id: UUID,
        purpose: AssetPurpose,
        condition: AssetCondition,
        availability: RentalAvailability,
        created_at: datetime,
        updated_at: datetime,
        retirement_reason: str | None = None,
    ) -> None:
        """Initialize state for controlled construction through ``create``."""
        self._id = id
        self._asset_number = asset_number
        self._variant_id = variant_id
        self._intake_item_id = intake_item_id
        self._purpose = purpose
        self._condition = condition
        self._availability = availability
        self._created_at = created_at
        self._updated_at = updated_at
        self._retirement_reason = retirement_reason

    @property
    def id(self) -> UUID:
        """Return the stable technical identifier of this physical item."""
        return self._id

    @property
    def asset_number(self) -> str:
        """Return the immutable business-facing number of this physical item."""
        return self._asset_number

    @property
    def variant_id(self) -> UUID:
        """Return the catalog variant represented by this physical item."""
        return self._variant_id

    @property
    def intake_item_id(self) -> UUID:
        """Return the intake item through which this physical item entered Core."""
        return self._intake_item_id

    @property
    def purpose(self) -> AssetPurpose:
        """Return the current business purpose without exposing mutation."""
        return self._purpose

    @property
    def condition(self) -> AssetCondition:
        """Return the current physical condition without exposing mutation."""
        return self._condition

    @property
    def availability(self) -> RentalAvailability:
        """Return the current rental availability without exposing mutation."""
        return self._availability

    @property
    def created_at(self) -> datetime:
        """Return when this aggregate was created."""
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        """Return when the latest successful domain transition occurred."""
        return self._updated_at

    @property
    def retirement_reason(self) -> str | None:
        """Return the recorded terminal retirement reason, when present."""
        return self._retirement_reason

    @classmethod
    def create(
        cls,
        *,
        asset_id: UUID,
        asset_number: str,
        variant_id: UUID,
        intake_item_id: UUID,
        condition: AssetCondition,
        created_at: datetime | None = None,
    ) -> RentalAsset:
        """Create exactly one rental asset from one physical intake unit.

        Serviceable items enter the available rental pool. Damaged or unusable
        items retain their identity but start in maintenance and cannot be
        checked out.
        """
        normalized_number = asset_number.strip()
        if not normalized_number:
            raise InvalidAssetNumberError
        cls._require_condition(condition)
        timestamp = created_at or datetime.now(UTC)
        availability = (
            RentalAvailability.AVAILABLE
            if condition in _SERVICEABLE_CONDITIONS
            else RentalAvailability.MAINTENANCE
        )
        return cls(
            id=asset_id,
            asset_number=normalized_number,
            variant_id=variant_id,
            intake_item_id=intake_item_id,
            purpose=AssetPurpose.RENTAL,
            condition=condition,
            availability=availability,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def checkout(self) -> None:
        """Mark an available, serviceable rental asset as issued to a customer."""
        if (
            self._purpose is not AssetPurpose.RENTAL
            or self._availability is not RentalAvailability.AVAILABLE
            or self._condition not in _SERVICEABLE_CONDITIONS
        ):
            self._raise_invalid_state("checkout")
        self._availability = RentalAvailability.RENTED
        self._touch()

    def accept_return(self, condition: AssetCondition) -> None:
        """Accept a rented asset and route it by its inspected condition.

        Serviceable returns become available immediately. Damaged or unusable
        returns move to maintenance so a return can never hide a defect.
        """
        self._require_condition(condition)
        if (
            self._purpose is not AssetPurpose.RENTAL
            or self._availability is not RentalAvailability.RENTED
        ):
            self._raise_invalid_state("accept return")
        self._condition = condition
        self._availability = (
            RentalAvailability.AVAILABLE
            if condition in _SERVICEABLE_CONDITIONS
            else RentalAvailability.MAINTENANCE
        )
        self._touch()

    def send_to_maintenance(self) -> None:
        """Remove an available rental asset from circulation for service."""
        if (
            self._purpose is not AssetPurpose.RENTAL
            or self._availability is not RentalAvailability.AVAILABLE
        ):
            self._raise_invalid_state("send to maintenance")
        self._availability = RentalAvailability.MAINTENANCE
        self._touch()

    def complete_maintenance(self, condition: AssetCondition) -> None:
        """Finish maintenance and record the resulting physical condition.

        A serviceable item returns to the available pool. An item that remains
        damaged or unusable stays in maintenance and remains unavailable.
        """
        self._require_condition(condition)
        if (
            self._purpose is not AssetPurpose.RENTAL
            or self._availability is not RentalAvailability.MAINTENANCE
        ):
            self._raise_invalid_state("complete maintenance")
        self._condition = condition
        self._availability = (
            RentalAvailability.AVAILABLE
            if condition in _SERVICEABLE_CONDITIONS
            else RentalAvailability.MAINTENANCE
        )
        self._touch()

    def withdraw_for_sale(self) -> None:
        """Move an available, usable item out of the rental pool for future sale."""
        if (
            self._purpose is not AssetPurpose.RENTAL
            or self._availability is not RentalAvailability.AVAILABLE
            or self._condition is AssetCondition.UNUSABLE
        ):
            self._raise_invalid_state("withdraw for sale")
        self._purpose = AssetPurpose.SALE
        self._touch()

    def retire(self, reason: str) -> None:
        """Permanently remove a non-rented asset from circulation.

        Retirement is terminal for ordinary domain commands. The mandatory
        reason preserves the current business explanation until append-only
        operation history is introduced in a later implementation stage.
        """
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise RetirementReasonRequiredError
        if (
            self._purpose is AssetPurpose.RETIRED
            or self._availability is RentalAvailability.RENTED
        ):
            self._raise_invalid_state("retire")
        self._purpose = AssetPurpose.RETIRED
        self._retirement_reason = normalized_reason
        self._touch()

    @staticmethod
    def _require_condition(condition: AssetCondition) -> None:
        """Reject runtime values that bypass the declared AssetCondition type."""
        if not isinstance(condition, AssetCondition):
            raise InvalidAssetConditionError

    def _raise_invalid_state(self, command: str) -> None:
        """Raise a state error containing all axes needed for diagnostics."""
        raise InvalidRentalStateError(
            command,
            purpose=self._purpose,
            condition=self._condition,
            availability=self._availability,
        )

    def _touch(self) -> None:
        """Record the time of the latest successful domain transition."""
        self._updated_at = datetime.now(UTC)

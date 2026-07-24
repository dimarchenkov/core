from __future__ import annotations

from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability


class RentalDomainError(Exception):
    """Base class for violations of Rental domain rules."""


class InvalidAssetNumberError(RentalDomainError):
    """Raised when a rental asset is created without a usable business number."""


class InvalidAssetConditionError(RentalDomainError):
    """Raised when a command receives an unsupported physical condition."""


class InvalidRentalStateError(RentalDomainError):
    """Raised when a RentalAsset command is invalid for its current state."""

    def __init__(
        self,
        command: str,
        *,
        purpose: AssetPurpose,
        condition: AssetCondition,
        availability: RentalAvailability,
    ) -> None:
        """Describe the rejected command and the complete current asset state."""
        self.command = command
        self.purpose = purpose
        self.condition = condition
        self.availability = availability
        super().__init__(
            f"Cannot {command} rental asset with purpose={purpose.value}, "
            f"condition={condition.value}, availability={availability.value}."
        )


class RetirementReasonRequiredError(RentalDomainError):
    """Raised when an asset retirement has no meaningful business reason."""

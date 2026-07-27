from core.rental.exceptions import RentalDomainError


class RentalOrderDomainError(RentalDomainError):
    """Base class for RentalOrder business rule violations."""


class InvalidRentalOrderDataError(RentalOrderDomainError):
    """Raised when required order or item snapshots are empty."""


class InvalidRentalPeriodError(RentalOrderDomainError):
    """Raised when planned return is not later than planned start."""


class InvalidRentalMoneyError(RentalOrderDomainError):
    """Raised when a rental monetary value violates domain rules."""


class InvalidRentalOrderStateError(RentalOrderDomainError):
    """Raised when an order command is invalid in its current lifecycle state."""


class RentalOrderHasNoItemsError(RentalOrderDomainError):
    """Raised when an empty draft is issued."""


class DuplicateRentalAssetError(RentalOrderDomainError):
    """Raised when one physical asset is added to an order twice."""


class RentalOrderItemNotFoundError(RentalOrderDomainError):
    """Raised when an order does not contain the requested item."""


class InvalidRentalOrderItemStateError(RentalOrderDomainError):
    """Raised when an item command is invalid in its current state."""

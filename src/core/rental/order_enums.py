from enum import StrEnum


class RentalOrderStatus(StrEnum):
    """Lifecycle state of one rental order aggregate."""

    DRAFT = "draft"
    ISSUED = "issued"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class RentalOrderItemStatus(StrEnum):
    """Lifecycle state of one physical asset inside a rental order."""

    PREPARED = "prepared"
    ISSUED = "issued"
    RETURNED = "returned"
    LOST = "lost"
    CANCELLED = "cancelled"

from enum import StrEnum


class RentalOrderStatus(StrEnum):
    """Lifecycle state of one rental order aggregate."""

    DRAFT = "draft"
    READY = "ready"
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


class RentalTimingSource(StrEnum):
    """Source used to establish one Rental's contractual period."""

    TARIFF = "tariff"
    MANUAL = "manual"


class RentalReturnCondition(StrEnum):
    """Operator inspection result captured when one item leaves an active rental."""

    GOOD = "good"
    DAMAGED = "damaged"
    INCOMPLETE = "incomplete"
    LOST = "lost"


class RentalOverdueState(StrEnum):
    """Timestamp-derived lateness state at preview or authoritative return time."""

    ON_TIME = "on_time"
    OVERDUE_GRACE = "overdue_grace"
    OVERDUE_CHARGEABLE = "overdue_chargeable"


class RentalOverduePolicy(StrEnum):
    """Deterministic policy frozen with return calculations."""

    NEXT_PERIOD = "next_period"

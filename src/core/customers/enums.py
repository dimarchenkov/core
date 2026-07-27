from enum import StrEnum


class CustomerStatus(StrEnum):
    """Lifecycle state controlling whether a customer can enter new rentals."""

    ACTIVE = "active"
    INACTIVE = "inactive"

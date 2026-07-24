from enum import StrEnum


class AssetPurpose(StrEnum):
    """Current business purpose assigned to one tracked physical item."""

    RENTAL = "rental"
    SALE = "sale"
    RETIRED = "retired"


class AssetCondition(StrEnum):
    """Observed physical condition of one rental asset."""

    NEW = "new"
    GOOD = "good"
    FAIR = "fair"
    DAMAGED = "damaged"
    UNUSABLE = "unusable"


class RentalAvailability(StrEnum):
    """Current operational availability of an asset in the rental lifecycle."""

    AVAILABLE = "available"
    RENTED = "rented"
    MAINTENANCE = "maintenance"

from __future__ import annotations

from enum import StrEnum


class RentalTariffType(StrEnum):
    """Supported ways to define one rental period."""

    DURATION = "duration"
    SLOT = "slot"


class RentalTariffStatus(StrEnum):
    """Lifecycle of one immutable commercial tariff version."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"

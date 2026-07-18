from __future__ import annotations

from enum import StrEnum


class PriceType(StrEnum):
    """Supported business meanings for sellable variant prices."""

    RETAIL = "retail"
    PROMO = "promo"

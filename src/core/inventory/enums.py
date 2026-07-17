from __future__ import annotations

from enum import StrEnum


class MovementType(StrEnum):
    """Business reasons for an immutable inventory quantity change."""

    RECEIPT = "receipt"
    SALE = "sale"
    RETURN = "return"
    ADJUSTMENT = "adjustment"
    WRITE_OFF = "write_off"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    REVERSAL = "reversal"


class SourceType(StrEnum):
    """Domain sources that explain why an inventory movement exists."""

    RECEIPT = "receipt"
    SALE = "sale"
    INVENTORY = "inventory"
    SYSTEM = "system"

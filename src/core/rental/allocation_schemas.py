from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from core.shared.db import UUIDv7


class InventoryAdjustmentReason(StrEnum):
    """Operator-visible reasons for an append-only manual correction."""

    SHORTAGE = "shortage"
    DAMAGE = "damage"
    GIFT = "gift"
    PERSONAL_USE = "personal_use"
    STOCKTAKE = "stocktake"
    OTHER = "other"


class RentalAllocationCreate(BaseModel):
    """Requested number of ordinary units to allocate to Rental."""

    quantity: int = Field(gt=0)


class RentalAllocationRead(BaseModel):
    """Stable identities created by one allocation."""

    asset_ids: list[UUIDv7]
    asset_numbers: list[str]


class InventoryAdjustmentCreate(BaseModel):
    """Administrator command for an attributed ledger correction."""

    model_config = ConfigDict(extra="forbid")

    quantity_delta: Decimal
    reason: InventoryAdjustmentReason
    comment: str | None = Field(default=None, max_length=1000)


class InventoryAdjustmentRead(BaseModel):
    """Created ledger identity and resulting physical balance."""

    movement_id: UUIDv7
    balance: Decimal

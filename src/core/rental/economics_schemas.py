from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.shared.db import UUIDv7


class EfficiencyFlag(StrEnum):
    """Computed operational signals; never persisted as asset statuses."""
    PAID_BACK = "paid_back"
    NOT_PAID_BACK = "not_paid_back"
    NEVER_RENTED = "never_rented"
    HIGH_EXPENSES = "high_expenses"
    LONG_IDLE = "long_idle"


class RentalAssetEconomicsRead(PydanticBaseModel):
    """Computed economics for one physical rental asset."""
    model_config = ConfigDict(extra="forbid")

    asset_id: UUIDv7
    acquisition_cost: Decimal | None
    rental_count: int
    total_rental_days: Decimal
    revenue: Decimal
    average_rental_revenue: Decimal
    expenses: Decimal
    net_income: Decimal
    last_rental_at: datetime | None
    payback_at: datetime | None
    flags: list[EfficiencyFlag]


class RentalVariantEconomicsRead(PydanticBaseModel):
    """Aggregated economics for one catalog variant."""
    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7
    asset_count: int
    active_asset_count: int
    available_asset_count: int
    revenue: Decimal
    expenses: Decimal
    profit: Decimal
    rental_count: int
    average_utilization: Decimal


class RentalProductEconomicsRead(PydanticBaseModel):
    """Aggregated economics for one product family."""
    model_config = ConfigDict(extra="forbid")

    product_id: UUIDv7
    asset_count: int
    revenue: Decimal
    expenses: Decimal
    profit: Decimal
    rental_count: int

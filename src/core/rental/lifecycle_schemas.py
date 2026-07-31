from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

from core.customers.enums import CustomerStatus
from core.rental.economics_schemas import RentalAssetEconomicsRead
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.order_enums import RentalOrderStatus
from core.shared.db import UUIDv7


class MaintenanceType(StrEnum):
    """Initial extensible vocabulary for physical asset servicing."""

    PREVENTIVE = "preventive"
    REPAIR = "repair"
    CLEANING = "cleaning"
    PART_REPLACEMENT = "part_replacement"


class DamageSeverity(StrEnum):
    """Operational severity of one observed damage record."""

    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CRITICAL = "critical"


class ConditionPhotoStage(StrEnum):
    """Moment represented by a condition photo."""

    BEFORE = "before"
    AFTER = "after"


class MaintenanceCreate(PydanticBaseModel):
    """Payload for appending one maintenance journal entry."""

    model_config = ConfigDict(extra="forbid")

    performed_at: datetime | None = None
    service_type: MaintenanceType
    comment: str | None = Field(default=None, max_length=2000)
    result: str = Field(min_length=1, max_length=2000)
    cost: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("result")
    @classmethod
    def normalize_result(cls, value: str) -> str:
        """Reject whitespace-only maintenance results."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Maintenance result is required.")
        return normalized


class MaintenanceRead(PydanticBaseModel):
    """One immutable maintenance fact with employee display data."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    performed_at: datetime
    service_type: str
    comment: str | None
    performer_id: UUIDv7 | None
    performer_name: str | None
    result: str
    cost: Decimal


class DamageCreate(PydanticBaseModel):
    """Payload for recording damage independently of the return command."""

    model_config = ConfigDict(extra="forbid")

    order_item_id: UUIDv7 | None = None
    description: str = Field(min_length=1, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    severity: DamageSeverity

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        """Reject whitespace-only damage descriptions."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Damage description is required.")
        return normalized


class DamageRead(PydanticBaseModel):
    """One immutable damage observation."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    order_item_id: UUIDv7 | None
    order_id: UUIDv7 | None
    order_number: str | None
    description: str
    comment: str | None
    severity: str
    recorded_by_id: UUIDv7 | None
    recorded_by_name: str | None
    created_at: datetime


class ConditionPhotoRead(PydanticBaseModel):
    """Minimal before/after photo reference for a physical asset."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    image_id: UUIDv7
    order_item_id: UUIDv7 | None
    stage: str
    recorded_by_id: UUIDv7 | None
    created_at: datetime


class AssetTimelineEvent(PydanticBaseModel):
    """One derived chronological event in the physical asset passport."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    occurred_at: datetime
    title: str
    detail: str | None = None
    order_id: UUIDv7 | None = None
    order_number: str | None = None
    customer_id: UUIDv7 | None = None
    customer_name: str | None = None


class RentalAssetPassportRead(PydanticBaseModel):
    """Complete operational passport of one physical rental asset."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    asset_number: str
    product_id: UUIDv7
    product_title: str
    variant_id: UUIDv7
    variant_title: str
    sku: str
    purpose: AssetPurpose
    condition: AssetCondition
    availability: RentalAvailability
    retirement_reason: str | None
    created_at: datetime
    rental_count: int
    completed_rental_count: int
    current_order_id: UUIDv7 | None
    current_order_number: str | None
    timeline: list[AssetTimelineEvent]
    maintenance: list[MaintenanceRead]
    damages: list[DamageRead]
    condition_photos: list[ConditionPhotoRead]
    economics: RentalAssetEconomicsRead


class CustomerRentalHistoryItem(PydanticBaseModel):
    """One customer rental row with quick contract navigation."""

    model_config = ConfigDict(extra="forbid")

    order_id: UUIDv7
    order_number: str
    status: RentalOrderStatus
    planned_start_at: datetime
    planned_return_at: datetime
    issued_at: datetime | None
    closed_at: datetime | None
    item_count: int


class CustomerRentalHistoryRead(PydanticBaseModel):
    """Derived active and completed rental history of one customer."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUIDv7
    customer_number: str
    full_name: str
    phone: str
    status: CustomerStatus
    rental_count: int
    active_rentals: list[CustomerRentalHistoryItem]
    completed_rentals: list[CustomerRentalHistoryItem]
    current_debt_amount: str | None = None

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, computed_field, model_validator

from core.rental.enums import AssetCondition
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.shared.db import UUIDv7


class RentalOrderCreate(PydanticBaseModel):
    """Payload for opening an empty rental order draft."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUIDv7
    planned_start_at: datetime
    planned_return_at: datetime
    deposit_amount: Decimal = Field(default=Decimal("0"))
    note: str | None = None


class RentalOrderUpdate(PydanticBaseModel):
    """Payload for changing mutable draft details."""

    model_config = ConfigDict(extra="forbid")

    planned_start_at: datetime | None = None
    planned_return_at: datetime | None = None
    deposit_amount: Decimal | None = None
    note: str | None = None


class RentalOrderItemCreate(PydanticBaseModel):
    """Payload for adding one physical asset to a draft."""

    model_config = ConfigDict(extra="forbid")

    rental_asset_id: UUIDv7
    agreed_price: Decimal
    discount: Decimal = Decimal("0")


class RentalOrderItemReturn(PydanticBaseModel):
    """Payload for completing one item as physically returned."""

    model_config = ConfigDict(extra="forbid")

    condition: AssetCondition
    charged_amount: Decimal
    returned_at: datetime | None = None
    return_note: str | None = None


class RentalOrderItemOutcome(StrEnum):
    """Terminal outcome selected by the return workflow."""

    RETURNED = "returned"
    LOST = "lost"


class RentalOrderItemCompletion(PydanticBaseModel):
    """One inspected item completed inside an atomic return workflow."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUIDv7
    outcome: RentalOrderItemOutcome
    condition: AssetCondition | None = None
    charged_amount: Decimal = Field(ge=0)
    completed_at: datetime | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_outcome_details(self) -> RentalOrderItemCompletion:
        """Require a physical condition only for items that were returned."""
        if self.outcome is RentalOrderItemOutcome.RETURNED and self.condition is None:
            raise ValueError("condition is required for a returned item")
        if self.outcome is RentalOrderItemOutcome.LOST and self.condition is not None:
            raise ValueError("condition must be omitted for a lost item")
        return self


class RentalOrderItemsComplete(PydanticBaseModel):
    """Batch of terminal item outcomes committed as one transaction."""

    model_config = ConfigDict(extra="forbid")

    items: list[RentalOrderItemCompletion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_items(self) -> RentalOrderItemsComplete:
        """Reject duplicate item commands inside one batch."""
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("each item may be completed only once")
        return self


class RentalOrderItemRead(PydanticBaseModel):
    """Nested RentalOrderItem representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    rental_asset_id: UUIDv7
    asset_number_snapshot: str
    title_snapshot: str
    agreed_price: Decimal
    discount: Decimal
    charged_amount: Decimal | None
    returned_at: datetime | None
    return_note: str | None
    status: RentalOrderItemStatus


class RentalOrderRead(PydanticBaseModel):
    """Complete RentalOrder aggregate representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    order_number: str
    customer_id: UUIDv7
    customer_name_snapshot: str
    customer_phone_snapshot: str
    status: RentalOrderStatus
    planned_start_at: datetime
    planned_return_at: datetime
    issued_at: datetime | None
    closed_at: datetime | None
    deposit_amount: Decimal
    note: str | None
    items: list[RentalOrderItemRead]

    @computed_field
    @property
    def is_overdue(self) -> bool:
        """Derive overdue state without persisting another order status."""
        if self.status is not RentalOrderStatus.ISSUED:
            return False
        now = datetime.now(UTC)
        if self.planned_return_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return self.planned_return_at < now

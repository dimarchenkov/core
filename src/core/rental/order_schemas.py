from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

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

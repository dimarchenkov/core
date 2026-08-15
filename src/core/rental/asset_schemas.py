from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.rental.enums import AssetCondition, RentalAvailability
from core.shared.db import UUIDv7


class RentalAssetRead(PydanticBaseModel):
    """Physical rental asset representation used by operational workflows."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    asset_number: str
    variant_id: UUIDv7
    product_title: str
    variant_title: str
    condition: AssetCondition
    availability: RentalAvailability
    suggested_rental_price: Decimal | None
    recommended_deposit: Decimal | None

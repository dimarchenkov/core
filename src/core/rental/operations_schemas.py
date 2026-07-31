from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.rental.enums import AssetCondition, RentalAvailability
from core.rental.order_enums import RentalOrderStatus
from core.shared.db import UUIDv7


class CatalogOperationsFilter(StrEnum):
    """Rental-aware filters for the operational product catalog."""

    ALL = "all"
    RENTAL = "rental"
    AVAILABLE = "available"
    NEEDS_PRICE = "needs_price"


class RentalAssetOperationsFilter(StrEnum):
    """Derived operational states exposed by the RentalAsset catalog."""

    ALL = "all"
    AVAILABLE = "available"
    RENTED = "rented"
    MAINTENANCE = "maintenance"
    LOST = "lost"


class RentalAssetOperationsSort(StrEnum):
    """Stable sort modes supported by the RentalAsset catalog."""

    ASSET_NUMBER = "asset_number"
    PRODUCT = "product"
    STATUS = "status"


class CatalogProductOperationsRead(PydanticBaseModel):
    """Product row enriched with rental availability counts."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    title: str
    description: str | None
    is_active: bool
    skus: list[str]
    variant_count: int
    rental_asset_count: int
    available_asset_count: int
    primary_image_id: UUIDv7 | None
    needs_initial_price: bool


class CatalogVariantOperationsRead(PydanticBaseModel):
    """Variant row used inside the operational product card."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    title: str
    sku: str
    barcode: str
    is_active: bool
    rental_asset_count: int
    available_asset_count: int
    primary_image_id: UUIDv7 | None
    current_retail_price: Decimal | None
    retail_currency: str | None
    has_ever_retail_price: bool


class RentalAssetOperationsRead(PydanticBaseModel):
    """RentalAsset row with catalog identity and current rental link."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    asset_number: str
    product_id: UUIDv7
    product_title: str
    variant_id: UUIDv7
    variant_title: str
    sku: str
    condition: AssetCondition
    availability: RentalAvailability
    is_lost: bool
    current_order_id: UUIDv7 | None
    current_order_number: str | None
    current_order_status: RentalOrderStatus | None


class CatalogProductOperationsDetail(CatalogProductOperationsRead):
    """Complete operational product card with variants and physical assets."""

    variants: list[CatalogVariantOperationsRead]
    rental_assets: list[RentalAssetOperationsRead]

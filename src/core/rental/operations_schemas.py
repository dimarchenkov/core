from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.catalog.barcodes import BarcodeSource
from core.integrations.aqsi.enums import PublicationStatus
from core.rental.economics_schemas import (
    EfficiencyFlag,
    RentalAssetEconomicsRead,
    RentalProductEconomicsRead,
    RentalVariantEconomicsRead,
)
from core.rental.enums import AssetCondition, RentalAvailability
from core.rental.order_enums import RentalOrderStatus
from core.shared.db import UUIDv7


class CatalogOperationsFilter(StrEnum):
    """Rental-aware filters for the operational product catalog."""

    ALL = "all"
    RENTAL = "rental"
    AVAILABLE = "available"
    NEEDS_PRICE = "needs_price"
    NEVER_RENTED = "never_rented"
    PAID_BACK = "paid_back"
    HIGH_EXPENSES = "high_expenses"
    LONG_IDLE = "long_idle"


class CatalogMode(StrEnum):
    """Top-level presentation modes for the shared operational Catalog."""

    SALE = "sale"
    RENTAL = "rental"
    ALL = "all"


class CatalogStatus(StrEnum):
    """Archive visibility for the shared operational Catalog."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    ALL = "all"


class CatalogAttentionFilter(StrEnum):
    """Operational conditions that can require a Catalog operator's attention."""

    MISSING_PRICE = "missing_price"
    MISSING_PHOTO = "missing_photo"
    AQSI_PROBLEM = "aqsi_problem"
    OUT_OF_STOCK = "out_of_stock"


class CatalogOperationsSort(StrEnum):
    """Computed economic sorting for the operational catalog."""

    TITLE = "title"
    REVENUE = "revenue"
    RENTAL_COUNT = "rental_count"
    PROFIT = "profit"
    LAST_RENTAL = "last_rental"


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
    REVENUE = "revenue"
    RENTAL_COUNT = "rental_count"
    PROFIT = "profit"
    LAST_RENTAL = "last_rental"


class CatalogVariantCardRead(PydanticBaseModel):
    """Commercial and operational Variant facts shown in a Catalog list card."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    title: str | None
    sku: str
    is_archived: bool
    current_retail_price: Decimal | None
    retail_currency: str | None
    current_rental_price: Decimal | None
    rental_currency: str | None
    stock_balance: Decimal
    sale_quantity: Decimal
    rental_asset_count: int
    sale_row_visible: bool
    rental_row_visible: bool
    aqsi_status: PublicationStatus | None
    aqsi_is_current: bool


class CatalogProductOperationsRead(PydanticBaseModel):
    """Product row enriched with rental availability counts."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    title: str
    description: str | None
    category_id: UUIDv7
    category_label: str
    is_active: bool
    is_archived: bool
    is_test: bool
    skus: list[str]
    variant_count: int
    card_variants: list[CatalogVariantCardRead]
    rental_asset_count: int
    rental_economics_applicable: bool
    available_asset_count: int
    primary_image_id: UUIDv7 | None
    needs_initial_price: bool
    economics: RentalProductEconomicsRead
    last_rental_at: datetime | None
    efficiency_flags: list[EfficiencyFlag]


class CatalogVariantOperationsRead(PydanticBaseModel):
    """Variant row used inside the operational product card."""

    model_config = ConfigDict(extra="forbid")

    id: UUIDv7
    title: str
    sku: str
    barcode: str
    barcode_source: BarcodeSource
    attributes: dict[str, str | int | bool]
    is_active: bool
    is_archived: bool
    physical_quantity: Decimal
    ordinary_quantity: Decimal
    rental_asset_count: int
    available_asset_count: int
    rented_asset_count: int
    primary_image_id: UUIDv7 | None
    current_retail_price: Decimal | None
    retail_currency: str | None
    current_rental_price: Decimal | None
    current_recommended_deposit: Decimal | None
    has_ever_retail_price: bool
    economics: RentalVariantEconomicsRead


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
    economics: RentalAssetEconomicsRead


class CatalogProductOperationsDetail(CatalogProductOperationsRead):
    """Complete operational product card with variants and physical assets."""

    variants: list[CatalogVariantOperationsRead]
    rental_assets: list[RentalAssetOperationsRead]

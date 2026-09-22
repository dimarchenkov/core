from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, computed_field, field_validator

from core.catalog.barcodes import (
    BarcodeFormat,
    BarcodeSource,
    detect_barcode_format,
    normalize_barcode,
)
from core.shared.db import UUIDv7


class CategoryBase(PydanticBaseModel):
    """Shared category fields accepted by API schemas."""

    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    parent_id: UUIDv7 | None = None
    sort_order: int = 0
    is_active: bool = True


class CategoryCreate(CategoryBase):
    """Payload for creating a catalog category."""


class CategoryUpdate(PydanticBaseModel):
    """Payload for updating a catalog category."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: UUIDv7 | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CategoryRead(CategoryBase):
    """Category representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    created_at: datetime
    updated_at: datetime
    version: int


class CatalogProductBase(PydanticBaseModel):
    """Shared catalog product fields accepted by API schemas."""

    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category_id: UUIDv7
    is_active: bool = True


class CatalogProductCreate(CatalogProductBase):
    """Payload for creating a catalog product."""


class CatalogProductUpdate(PydanticBaseModel):
    """Payload for updating a catalog product."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category_id: UUIDv7 | None = None
    is_active: bool | None = None


class CatalogProductRead(CatalogProductBase):
    """Catalog product representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    created_at: datetime
    updated_at: datetime
    version: int


class CatalogVariantBase(PydanticBaseModel):
    """Shared catalog variant fields accepted by API schemas."""

    product_id: UUIDv7
    title: str = Field(min_length=1, max_length=255)
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)
    is_active: bool = True


class CatalogVariantCreate(CatalogVariantBase):
    """Payload for creating a catalog variant without a user-supplied SKU."""

    model_config = ConfigDict(extra="forbid")

    manufacturer_barcode: str | None = Field(default=None, max_length=128)

    @field_validator("manufacturer_barcode")
    @classmethod
    def validate_manufacturer_barcode(cls, value: str | None) -> str | None:
        """Normalize and validate an optional manufacturer code."""
        return normalize_barcode(value) if value is not None else None


class CatalogVariantUpdate(PydanticBaseModel):
    """Payload for updating mutable catalog variant fields."""

    model_config = ConfigDict(extra="forbid")

    product_id: UUIDv7 | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    attributes: dict[str, str | int | bool] | None = None
    is_active: bool | None = None


class CatalogVariantBarcodeRead(PydanticBaseModel):
    """One typed barcode registered to a Variant."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    value: str
    source: BarcodeSource
    @computed_field
    @property
    def format(self) -> BarcodeFormat:
        """Expose the detected symbology without persisting derived data."""
        return detect_barcode_format(self.value)


class CatalogVariantRead(CatalogVariantBase):
    """Catalog variant representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    sku: str
    barcode: str
    barcode_source: BarcodeSource
    created_at: datetime
    updated_at: datetime
    version: int


class CatalogVariantSearchResult(PydanticBaseModel):
    """Compact existing-Variant projection for operational pickers."""

    id: UUIDv7
    product_id: UUIDv7
    product_title: str
    title: str
    sku: str
    barcode: str
    retail_price: Decimal | None = None


class CatalogProductSearchResult(PydanticBaseModel):
    """Compact parent-Product projection for the new-Variant workflow."""

    id: UUIDv7
    title: str
    variant_count: int
    matched_variant_title: str | None = None
    matched_sku: str | None = None
    matched_barcode: str | None = None


class CatalogVariantSearchPage(PydanticBaseModel):
    """Bounded Variant search response."""

    items: list[CatalogVariantSearchResult]
    has_more: bool


class CatalogProductSearchPage(PydanticBaseModel):
    """Bounded, deduplicated Product search response."""

    items: list[CatalogProductSearchResult]
    has_more: bool


class CatalogVariantBarcodeCreate(PydanticBaseModel):
    """Register an additional manufacturer barcode on an existing Variant."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=128)
    source: BarcodeSource = BarcodeSource.MANUFACTURER

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        """Normalize and validate the registered code."""
        return normalize_barcode(value)


class CatalogVariantBarcodeReplace(PydanticBaseModel):
    """Replace the one operational barcode with an operator-supplied external code."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=128)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        """Normalize and validate the replacement operational barcode."""
        return normalize_barcode(value)

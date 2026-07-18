from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

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


class CatalogVariantUpdate(PydanticBaseModel):
    """Payload for updating mutable catalog variant fields."""

    model_config = ConfigDict(extra="forbid")

    product_id: UUIDv7 | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    attributes: dict[str, str | int | bool] | None = None
    is_active: bool | None = None


class CatalogVariantRead(CatalogVariantBase):
    """Catalog variant representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    sku: str
    barcode: str
    created_at: datetime
    updated_at: datetime
    version: int

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

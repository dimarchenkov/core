from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

from core.shared.db import UUIDv7


class SupplierCreate(PydanticBaseModel):
    """Public payload for registering a supplier without a caller-provided code."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    is_active: bool = True


class SupplierUpdate(PydanticBaseModel):
    """Public payload for changing mutable supplier reference fields."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    is_active: bool | None = None


class SupplierRead(PydanticBaseModel):
    """Supplier representation returned by purchasing API routes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    name: str
    display_name: str | None
    code: str
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

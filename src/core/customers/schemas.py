from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

from core.customers.enums import CustomerStatus
from core.shared.db import UUIDv7


class CustomerCreate(PydanticBaseModel):
    """Payload for registering a customer without caller-owned identifiers."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(max_length=255)
    phone: str = Field(max_length=32)
    email: str | None = Field(default=None, max_length=320)
    note: str | None = None


class CustomerUpdate(PydanticBaseModel):
    """Payload for changing current customer contact data."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    note: str | None = None


class CustomerRead(PydanticBaseModel):
    """Customer representation returned by the Customers API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    customer_number: str
    full_name: str
    phone: str
    email: str | None
    note: str | None
    status: CustomerStatus
    created_at: datetime
    updated_at: datetime

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

from core.receipt.enums import ReceiptStatus
from core.shared.db import UUIDv7

MoneyAmount = Annotated[Decimal, Field(ge=Decimal("0"), max_digits=12)]


class ReceiptCreate(PydanticBaseModel):
    """Public payload for opening a new draft receipt."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUIDv7
    receipt_date: date
    source_document_number: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class ReceiptUpdate(PydanticBaseModel):
    """Public payload for editing mutable draft receipt fields."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUIDv7 | None = None
    receipt_date: date | None = None
    source_document_number: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class ReceiptRead(PydanticBaseModel):
    """Draft receipt representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    number: str
    supplier_id: UUIDv7
    receipt_date: date
    status: ReceiptStatus
    source_document_number: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ReceiptItemCreate(PydanticBaseModel):
    """Public payload for adding an existing variant to a draft receipt."""

    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7
    quantity: int = Field(gt=0)
    purchase_price: MoneyAmount

    @field_validator("purchase_price", mode="before")
    @classmethod
    def reject_float_purchase_price(cls, value: object) -> object:
        """Reject binary floating-point input before Decimal conversion."""
        if isinstance(value, float):
            raise ValueError("Purchase price must be a Decimal-compatible string or integer.")
        return value


class ReceiptItemUpdate(PydanticBaseModel):
    """Public payload for editing a draft receipt line."""

    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7 | None = None
    quantity: int | None = Field(default=None, gt=0)
    purchase_price: MoneyAmount | None = None

    @field_validator("purchase_price", mode="before")
    @classmethod
    def reject_float_purchase_price(cls, value: object) -> object:
        """Reject binary floating-point input before Decimal conversion."""
        if isinstance(value, float):
            raise ValueError("Purchase price must be a Decimal-compatible string or integer.")
        return value


class ReceiptItemRead(PydanticBaseModel):
    """Receipt line representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    receipt_id: UUIDv7
    variant_id: UUIDv7
    quantity: int
    purchase_price: Decimal
    created_at: datetime
    updated_at: datetime

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator, model_validator

from core.intake.enums import (
    IntakeItemKind,
    IntakeItemRequirement,
    IntakeSessionRequirement,
    IntakeSessionStatus,
)
from core.readiness.schemas import ReadyForSaleRead
from core.receipt.schemas import ReceiptRead
from core.shared.db import UUIDv7


class IntakeCreate(PydanticBaseModel):
    """Payload for creating a product, variant, and primary image link together."""

    category_id: UUIDv7
    product_title: str = Field(min_length=1, max_length=255)
    product_description: str | None = None
    variant_title: str = Field(min_length=1, max_length=255)
    attributes: dict[str, str | int | bool] = Field(default_factory=dict)
    image_id: UUIDv7


class IntakeRead(PydanticBaseModel):
    """Identifiers returned after a successful atomic intake."""

    product_id: UUIDv7
    variant_id: UUIDv7
    sku: str
    image_link_id: UUIDv7


class IntakeSessionUpdate(PydanticBaseModel):
    """Mutable IntakeSession fields that may be supplied after identification."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: UUIDv7 | None = None


class ExistingIntakeItemCreate(PydanticBaseModel):
    """Identify a repeat delivery by exactly one stable catalog identifier."""

    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7 | None = None
    barcode: str | None = Field(default=None, min_length=1, max_length=22)
    quantity: int | None = Field(default=None, gt=0)
    purchase_price: Decimal | None = Field(default=None, max_digits=12, ge=0)

    @model_validator(mode="after")
    def require_exactly_one_identifier(self) -> ExistingIntakeItemCreate:
        """Require either Variant ID or barcode without ambiguous precedence."""
        if (self.variant_id is None) == (self.barcode is None):
            raise ValueError("Provide exactly one of variant_id or barcode.")
        return self

    @field_validator("barcode")
    @classmethod
    def normalize_barcode(cls, value: str | None) -> str | None:
        """Trim a scanner value while preserving exact barcode semantics."""
        return value.strip() if value is not None else None

    @field_validator("purchase_price", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Require decimal-safe money input."""
        if isinstance(value, float):
            raise ValueError("Purchase price must be a decimal string or integer.")
        return value


class IntakeItemDraftUpdate(PydanticBaseModel):
    """Fields progressively supplied while an intake item remains a draft."""

    model_config = ConfigDict(extra="forbid")

    category_id: UUIDv7 | None = None
    product_title: str | None = Field(default=None, min_length=1, max_length=255)
    product_description: str | None = None
    variant_title: str | None = Field(default=None, min_length=1, max_length=255)
    attributes: dict[str, str | int | bool] | None = None
    quantity: int | None = Field(default=None, gt=0)
    purchase_price: Decimal | None = Field(default=None, max_digits=12, ge=0)

    @field_validator("purchase_price", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Require decimal-safe money input."""
        if isinstance(value, float):
            raise ValueError("Purchase price must be a decimal string or integer.")
        return value

    @field_validator("product_title", "variant_title")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        """Trim future required names while allowing an omitted draft field."""
        return value.strip() if value is not None else None


class IntakeAbandon(PydanticBaseModel):
    """Explicit reason for abandoning resumable operational work."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """Reject whitespace-only operational reasons."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Abandonment reason is required.")
        return normalized


class IntakeItemDraftRead(PydanticBaseModel):
    """Persisted intake item plus its derived missing requirements."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    session_id: UUIDv7
    kind: IntakeItemKind
    variant_id: UUIDv7 | None
    product_id: UUIDv7 | None
    image_id: UUIDv7 | None
    category_id: UUIDv7 | None
    product_title: str | None
    product_description: str | None
    variant_title: str | None
    attributes: dict[str, str | int | bool]
    quantity: int | None
    purchase_price: Decimal | None
    abandoned_at: datetime | None
    abandonment_reason: str | None
    created_at: datetime
    updated_at: datetime
    created_by_id: UUIDv7 | None
    updated_by_id: UUIDv7 | None
    missing_requirements: list[IntakeItemRequirement] = Field(default_factory=list)


class IntakeSessionRead(PydanticBaseModel):
    """Owned IntakeSession with resumable positions and derived completeness."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    owner_id: UUIDv7
    status: IntakeSessionStatus
    supplier_id: UUIDv7 | None
    receipt_id: UUIDv7 | None
    completed_at: datetime | None
    abandoned_at: datetime | None
    abandonment_reason: str | None
    created_at: datetime
    updated_at: datetime
    items: list[IntakeItemDraftRead]
    missing_requirements: list[IntakeSessionRequirement] = Field(default_factory=list)


class IntakeCompletionItemRead(PydanticBaseModel):
    """Stable mapping from an intake draft position to its catalog Variant."""

    item_id: UUIDv7
    product_id: UUIDv7
    variant_id: UUIDv7


class IntakeCompletionRead(PydanticBaseModel):
    """Result of an atomic IntakeSession completion command."""

    session_id: UUIDv7
    receipt: ReceiptRead
    items: list[IntakeCompletionItemRead]
    readiness: list[ReadyForSaleRead]

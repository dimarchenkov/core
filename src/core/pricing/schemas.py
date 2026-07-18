from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator

from core.pricing.enums import PriceType
from core.shared.db import UUIDv7
from core.shared.money import DEFAULT_CURRENCY


class PriceCreate(PydanticBaseModel):
    """Payload for appending one price fact to a variant's history."""

    model_config = ConfigDict(extra="forbid")

    price_type: PriceType
    amount: Decimal = Field(max_digits=12, ge=0)
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3)
    effective_from: datetime | None = None
    reason: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Reject imprecise binary floating-point money input."""
        if isinstance(value, float):
            raise ValueError("Money amounts must be decimal strings, integers, or Decimal values.")
        return value

    @field_validator("amount")
    @classmethod
    def require_finite_amount(cls, value: Decimal) -> Decimal:
        """Reject NaN and infinite money values."""
        if not value.is_finite():
            raise ValueError("Money amount must be finite.")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        """Normalize ISO-style currency codes before business validation."""
        return value.strip().upper()

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        """Store a missing reason instead of whitespace-only text."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PriceRead(PydanticBaseModel):
    """Immutable price fact returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    variant_id: UUIDv7
    price_type: PriceType
    amount: Decimal
    currency: str
    effective_from: datetime
    reason: str | None
    created_at: datetime
    created_by_id: UUIDv7 | None

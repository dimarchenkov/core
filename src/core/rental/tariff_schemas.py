from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, field_validator, model_validator

from core.rental.tariff_enums import RentalTariffStatus, RentalTariffType
from core.shared.db import UUIDv7


class RentalTariffWrite(PydanticBaseModel):
    """Shared authoritative input for creating a tariff or a new version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    type: RentalTariffType
    price: Decimal = Field(max_digits=12, ge=0)
    duration_minutes: int | None = None
    slot_start: time | None = None
    slot_end: time | None = None
    grace_minutes: int = Field(default=0, ge=0, le=10_080)
    deposit_amount: Decimal | None = Field(default=None, max_digits=12, ge=0)
    requires_agreement: bool = False
    valid_from: date
    valid_to: date | None = None

    @field_validator("price", "deposit_amount", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        """Reject imprecise binary floats for all money fields."""
        if isinstance(value, float):
            raise ValueError("Money must be sent as a decimal string or integer.")
        return value

    @field_validator("price", "deposit_amount")
    @classmethod
    def require_finite_money(cls, value: Decimal | None) -> Decimal | None:
        """Reject NaN and infinity before persistence."""
        if value is not None and not value.is_finite():
            raise ValueError("Money must be finite.")
        return value

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """Store a readable non-blank operator label."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tariff name is required.")
        return normalized

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> RentalTariffWrite:
        """Reject ambiguous duration/slot combinations and overnight slots."""
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        if self.type is RentalTariffType.DURATION:
            if self.duration_minutes is None or self.duration_minutes <= 0:
                raise ValueError("DURATION requires a positive duration_minutes")
            if self.slot_start is not None or self.slot_end is not None:
                raise ValueError("DURATION does not accept slot fields")
        else:
            if self.duration_minutes is not None:
                raise ValueError("SLOT duration is derived and cannot be operator-controlled")
            if self.slot_start is None or self.slot_end is None:
                raise ValueError("SLOT requires slot_start and slot_end")
            if self.slot_end <= self.slot_start:
                raise ValueError("Overnight or zero-length SLOT tariffs are not supported")
        return self


class RentalTariffCreate(RentalTariffWrite):
    """Payload for creating the first version of a tariff line."""


class RentalTariffUpdate(RentalTariffWrite):
    """Complete replacement terms used to create the next immutable version."""


class RentalTariffRead(PydanticBaseModel):
    """Operator-facing representation of one exact tariff version."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    variant_id: UUIDv7
    lineage_id: UUIDv7
    version: int
    name: str
    type: RentalTariffType
    price: Decimal
    duration_minutes: int | None
    slot_start: time | None
    slot_end: time | None
    grace_minutes: int
    deposit_amount: Decimal | None
    requires_agreement: bool
    valid_from: date
    valid_to: date | None
    status: RentalTariffStatus
    superseded_at: datetime | None
    locked_at: datetime | None
    created_at: datetime
    snapshot: dict[str, str | int | bool | None]
    tariff_terms: str

    @classmethod
    def from_record(cls, record: object) -> RentalTariffRead:
        """Build computed legal/operational representations from a record."""
        return cls.model_validate(
            {
                **{
                    field: getattr(record, field)
                    for field in cls.model_fields
                    if hasattr(record, field)
                },
                "snapshot": record.to_snapshot(),  # type: ignore[attr-defined]
                "tariff_terms": record.format_terms(),  # type: ignore[attr-defined]
            }
        )

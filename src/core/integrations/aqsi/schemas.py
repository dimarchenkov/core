from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationChannel,
    PublicationOperation,
    PublicationStatus,
)
from core.shared.db import UUIDv7


class AqsiGoodsPayload(PydanticBaseModel):
    """Minimal ordinary-piece-goods request accepted by AQSI V2."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    type: str = Field(default="simple", min_length=1)
    name: str = Field(min_length=1, max_length=128)
    tax: int = Field(ge=1, le=10)
    unit: str = Field(default="Штука", min_length=1, max_length=16)
    unit_code: int = Field(default=0, alias="unitCode")
    subject: int = Field(default=1)
    payment_method_type: int = Field(default=4, alias="paymentMethodType")
    sku: str = Field(min_length=1, max_length=64)
    price: float = Field(gt=0)
    barcodes: list[str] = Field(min_length=1)

    def as_aqsi_json(self) -> dict[str, object]:
        """Return a JSON-ready dictionary using AQSI field names."""
        return self.model_dump(mode="json", by_alias=True)


class AqsiDefaultCategoryPayload(PydanticBaseModel):
    """Default AQSI category created during first publication bootstrap."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=256)
    default_subject: int = Field(default=1, alias="defaultSubject")
    default_tax: int = Field(alias="defaultTax", ge=1, le=10)
    default_unit: str = Field(default="Штука", alias="defaultUnit", max_length=16)
    default_unit_code: int = Field(default=0, alias="defaultUnitCode")
    default_payment_method_type: int = Field(default=4, alias="defaultPaymentMethodType")

    def as_aqsi_json(self) -> dict[str, object]:
        """Return a JSON-ready dictionary using AQSI field names."""
        return self.model_dump(mode="json", by_alias=True)


class AqsiShopPricePayload(PydanticBaseModel):
    """AQSI request that binds one good to one shop at its retail price."""

    id: str = Field(min_length=1)
    shops: list[dict[str, object]] = Field(min_length=1)
    default_price: float = Field(alias="defaultPrice", gt=0)

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def for_good(cls, good_id: str, shop_id: str, price: float) -> AqsiShopPricePayload:
        """Build one deterministic single-shop price binding."""
        return cls(
            id=good_id,
            shops=[{"id": shop_id, "price": price}],
            defaultPrice=price,
        )

    def as_aqsi_json(self) -> dict[str, object]:
        """Return a JSON-ready dictionary using AQSI field names."""
        return self.model_dump(mode="json", by_alias=True)


class PublicationAttemptRead(PydanticBaseModel):
    """Public operational state for one AQSI publication request."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    publication_id: UUIDv7
    operation: PublicationOperation
    status: PublicationAttemptStatus
    payload_hash: str
    attempt_number: int
    error_code: str | None
    error_message: str | None
    requested_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None
    created_by_id: UUIDv7 | None


class PublicationRead(PydanticBaseModel):
    """Current AQSI projection state for one Core Variant."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    variant_id: UUIDv7
    channel: PublicationChannel
    external_id: str
    status: PublicationStatus
    last_requested_payload_hash: str | None
    last_verified_payload_hash: str | None
    last_error: str | None
    published_at: datetime | None
    updated_at: datetime
    is_outdated: bool = False


class PublicationRequestRead(PydanticBaseModel):
    """Response returned when Core accepts an AQSI publication command."""

    publication: PublicationRead
    attempt: PublicationAttemptRead
    queued: bool

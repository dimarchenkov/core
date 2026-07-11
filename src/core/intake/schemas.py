from __future__ import annotations

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

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

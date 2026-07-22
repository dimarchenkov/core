from __future__ import annotations

from pydantic import BaseModel as PydanticBaseModel

from core.readiness.enums import ReadyForSaleRequirement
from core.shared.db import UUIDv7


class ReadyForSaleRead(PydanticBaseModel):
    """Derived readiness result for one sellable catalog variant."""

    variant_id: UUIDv7
    is_ready: bool
    missing_requirements: list[ReadyForSaleRequirement]


class ReadyForSaleAttentionItemRead(PydanticBaseModel):
    """One incomplete Variant in the employee attention queue."""

    variant_id: UUIDv7
    product_id: UUIDv7
    product_title: str
    variant_title: str
    sku: str
    barcode: str
    primary_image_id: UUIDv7 | None
    missing_requirements: list[ReadyForSaleRequirement]


class ReadyForSaleAttentionPage(PydanticBaseModel):
    """Stable paginated slice of the current derived attention queue."""

    items: list[ReadyForSaleAttentionItemRead]
    total: int
    limit: int
    offset: int

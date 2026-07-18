from __future__ import annotations

from enum import StrEnum


class ReadyForSaleRequirement(StrEnum):
    """Machine-readable reasons why a variant is not ready for sale."""

    INACTIVE_VARIANT = "inactive_variant"
    MISSING_PRIMARY_IMAGE = "missing_primary_image"
    MISSING_SKU = "missing_sku"
    MISSING_BARCODE = "missing_barcode"
    INVALID_BARCODE = "invalid_barcode"
    MISSING_RETAIL_PRICE = "missing_retail_price"

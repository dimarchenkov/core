from __future__ import annotations

from enum import StrEnum


class ImageLinkEntityType(StrEnum):
    """Catalog entity types that can receive image links."""

    CATALOG_PRODUCT = "catalog_product"
    CATALOG_VARIANT = "catalog_variant"


class ImageLinkRole(StrEnum):
    """Display roles available for an image linked to an entity."""

    PRIMARY = "primary"
    GALLERY = "gallery"

from __future__ import annotations

from sqlalchemy import func

from core.catalog.models import CatalogVariant


def variant_ordering_expressions() -> tuple[object, ...]:
    """Return the shared SQL order for Variants within their parent Product."""
    return (
        CatalogVariant.sort_order.is_(None),
        CatalogVariant.sort_order,
        func.lower(CatalogVariant.title),
        CatalogVariant.title,
        CatalogVariant.id,
    )


def variant_ordering_key(variant: CatalogVariant) -> tuple[bool, int, str, str, str]:
    """Mirror the effective database order for in-memory Variant projections."""
    return (
        variant.sort_order is None,
        variant.sort_order or 0,
        variant.title.casefold(),
        variant.title,
        str(variant.id),
    )

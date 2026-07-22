from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.policy import ReadyForSaleFacts, derive_ready_for_sale_requirements
from core.readiness.schemas import (
    ReadyForSaleAttentionItemRead,
    ReadyForSaleAttentionPage,
)


class ReadyForSaleReadService:
    """Build the employee attention queue from current authoritative facts."""

    def __init__(self, session: Session) -> None:
        """Create a read service bound to one request-scoped database session."""
        self._session = session

    def list_attention(
        self,
        *,
        requirement: ReadyForSaleRequirement | None = None,
        limit: int = 50,
        offset: int = 0,
        at: datetime | None = None,
    ) -> ReadyForSaleAttentionPage:
        """Return incomplete Variants in stable order with optional reason filtering."""
        checked_at = at or datetime.now(UTC)
        image_exists = (
            exists()
            .where(
                ImageLink.image_id == Image.id,
                ImageLink.entity_type == ImageLinkEntityType.CATALOG_VARIANT,
                ImageLink.entity_id == CatalogVariant.id,
                ImageLink.role == ImageLinkRole.PRIMARY,
                ImageLink.deleted_at.is_(None),
                Image.deleted_at.is_(None),
            )
            .correlate(CatalogVariant)
        )
        current_price = (
            select(Price)
            .where(
                Price.variant_id == CatalogVariant.id,
                Price.price_type == PriceType.RETAIL,
                Price.effective_from <= checked_at,
            )
            .order_by(
                Price.effective_from.desc(),
                Price.created_at.desc(),
                Price.id.desc(),
            )
            .limit(1)
            .correlate(CatalogVariant)
        )
        current_amount = current_price.with_only_columns(Price.amount).scalar_subquery()
        current_currency = current_price.with_only_columns(Price.currency).scalar_subquery()

        statement = (
            select(
                CatalogVariant.id,
                CatalogVariant.product_id,
                CatalogProduct.title.label("product_title"),
                CatalogVariant.title.label("variant_title"),
                CatalogVariant.sku,
                CatalogVariant.barcode,
                CatalogVariant.is_active,
                image_exists.label("has_primary_image"),
                current_amount.label("retail_amount"),
                current_currency.label("retail_currency"),
            )
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(CatalogVariant.deleted_at.is_(None))
            .order_by(
                CatalogProduct.title,
                CatalogVariant.title,
                CatalogVariant.id,
            )
        )

        items: list[ReadyForSaleAttentionItemRead] = []
        for row in self._session.execute(statement):
            missing = derive_ready_for_sale_requirements(
                ReadyForSaleFacts(
                    is_active=row.is_active,
                    has_primary_image=row.has_primary_image,
                    sku=row.sku,
                    barcode=row.barcode,
                    retail_amount=(
                        Decimal(row.retail_amount) if row.retail_amount is not None else None
                    ),
                    retail_currency=row.retail_currency,
                )
            )
            if not missing or (requirement is not None and requirement not in missing):
                continue
            items.append(
                ReadyForSaleAttentionItemRead(
                    variant_id=row.id,
                    product_id=row.product_id,
                    product_title=row.product_title,
                    variant_title=row.variant_title,
                    sku=row.sku,
                    barcode=row.barcode,
                    missing_requirements=missing,
                )
            )

        total = len(items)
        return ReadyForSaleAttentionPage(
            items=items[offset : offset + limit],
            total=total,
            limit=limit,
            offset=offset,
        )

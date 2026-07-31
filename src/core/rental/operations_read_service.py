from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.repository import PriceRepository
from core.rental.enums import RentalAvailability
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.operations_schemas import (
    CatalogOperationsFilter,
    CatalogProductOperationsDetail,
    CatalogProductOperationsRead,
    CatalogVariantOperationsRead,
    RentalAssetOperationsFilter,
    RentalAssetOperationsRead,
    RentalAssetOperationsSort,
)
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.shared.db import UUIDv7


class OperationsProductNotFoundError(Exception):
    """Raised when an operational product card does not exist."""


class RentalOperationsReadService:
    """Read-only projections joining Catalog and Rental for daily operations."""

    def __init__(self, session: Session) -> None:
        """Create projections from one request-scoped database session."""
        self._session = session

    def list_products(
        self,
        query: str | None = None,
        *,
        product_filter: CatalogOperationsFilter = CatalogOperationsFilter.ALL,
    ) -> list[CatalogProductOperationsRead]:
        """Return products with variant and physical rental counts."""
        statement = select(CatalogProduct).where(CatalogProduct.deleted_at.is_(None))
        normalized = (query or "").strip()
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    CatalogProduct.title.ilike(pattern),
                    CatalogProduct.variants.any(
                        or_(
                            CatalogVariant.title.ilike(pattern),
                            CatalogVariant.sku.ilike(pattern),
                            CatalogVariant.barcode.ilike(pattern),
                        )
                    ),
                )
            )
        products = self._session.scalars(statement.order_by(CatalogProduct.title)).all()
        product_ids = [product.id for product in products]
        variants = self._variants_for_products(product_ids)
        assets = self._asset_records_for_products(product_ids)
        variant_ids = [variant.id for variant in variants]
        image_ids = self._primary_images(products, variants)
        priced_variant_ids = self._ever_priced_variant_ids(variant_ids)
        variants_by_product: dict[UUIDv7, list[CatalogVariant]] = defaultdict(list)
        assets_by_product: dict[UUIDv7, list[RentalAssetRecord]] = defaultdict(list)
        for variant in variants:
            variants_by_product[variant.product_id].append(variant)
        variant_products = {variant.id: variant.product_id for variant in variants}
        for asset in assets:
            assets_by_product[variant_products[asset.variant_id]].append(asset)

        rows = [
            CatalogProductOperationsRead(
                id=product.id,
                title=product.title,
                description=product.description,
                is_active=product.is_active,
                skus=[variant.sku for variant in variants_by_product[product.id]],
                variant_count=len(variants_by_product[product.id]),
                rental_asset_count=len(assets_by_product[product.id]),
                available_asset_count=sum(
                    asset.availability is RentalAvailability.AVAILABLE
                    for asset in assets_by_product[product.id]
                ),
                primary_image_id=next(
                    (
                        image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                        for variant in variants_by_product[product.id]
                        if image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                    ),
                    image_ids.get((ImageLinkEntityType.CATALOG_PRODUCT, product.id)),
                ),
                needs_initial_price=any(
                    variant.id not in priced_variant_ids
                    for variant in variants_by_product[product.id]
                ),
            )
            for product in products
        ]
        if product_filter is CatalogOperationsFilter.RENTAL:
            return [row for row in rows if row.rental_asset_count > 0]
        if product_filter is CatalogOperationsFilter.AVAILABLE:
            return [row for row in rows if row.available_asset_count > 0]
        if product_filter is CatalogOperationsFilter.NEEDS_PRICE:
            return [row for row in rows if row.needs_initial_price]
        return rows

    def get_product(self, product_id: UUIDv7) -> CatalogProductOperationsDetail:
        """Return one product with all variants and RentalAssets."""
        product = self._session.scalar(
            select(CatalogProduct).where(
                CatalogProduct.id == product_id,
                CatalogProduct.deleted_at.is_(None),
            )
        )
        if product is None:
            raise OperationsProductNotFoundError
        variants = self._variants_for_products([product_id])
        image_ids = self._primary_images([product], variants)
        variant_ids = [variant.id for variant in variants]
        priced_variant_ids = self._ever_priced_variant_ids(variant_ids)
        prices = PriceRepository(self._session)
        now = datetime.now(UTC)
        assets = self.list_assets(product_id=product_id)
        assets_by_variant: dict[UUIDv7, list[RentalAssetOperationsRead]] = defaultdict(list)
        for asset in assets:
            assets_by_variant[asset.variant_id].append(asset)
        return CatalogProductOperationsDetail(
            id=product.id,
            title=product.title,
            description=product.description,
            is_active=product.is_active,
            skus=[variant.sku for variant in variants],
            variant_count=len(variants),
            rental_asset_count=len(assets),
            available_asset_count=sum(
                asset.availability is RentalAvailability.AVAILABLE for asset in assets
            ),
            primary_image_id=next(
                (
                    image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                    for variant in variants
                    if image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                ),
                image_ids.get((ImageLinkEntityType.CATALOG_PRODUCT, product.id)),
            ),
            needs_initial_price=any(
                variant.id not in priced_variant_ids for variant in variants
            ),
            variants=[
                CatalogVariantOperationsRead(
                    id=variant.id,
                    title=variant.title,
                    sku=variant.sku,
                    barcode=variant.barcode,
                    is_active=variant.is_active,
                    rental_asset_count=len(assets_by_variant[variant.id]),
                    available_asset_count=sum(
                        asset.availability is RentalAvailability.AVAILABLE
                        for asset in assets_by_variant[variant.id]
                    ),
                    primary_image_id=(
                        image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                        or image_ids.get((ImageLinkEntityType.CATALOG_PRODUCT, product.id))
                    ),
                    current_retail_price=(
                        current_price.amount if (current_price := prices.get_current(
                            variant.id, PriceType.RETAIL, at=now
                        )) is not None else None
                    ),
                    retail_currency=(current_price.currency if current_price is not None else None),
                    has_ever_retail_price=variant.id in priced_variant_ids,
                )
                for variant in variants
            ],
            rental_assets=assets,
        )

    def list_assets(
        self,
        query: str | None = None,
        *,
        asset_filter: RentalAssetOperationsFilter = RentalAssetOperationsFilter.ALL,
        sort: RentalAssetOperationsSort = RentalAssetOperationsSort.ASSET_NUMBER,
        product_id: UUIDv7 | None = None,
    ) -> list[RentalAssetOperationsRead]:
        """Return searchable assets with derived LOST and current-order links."""
        statement = (
            select(RentalAssetRecord, CatalogVariant, CatalogProduct)
            .join(CatalogVariant, CatalogVariant.id == RentalAssetRecord.variant_id)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(RentalAssetRecord.deleted_at.is_(None))
        )
        if product_id is not None:
            statement = statement.where(CatalogProduct.id == product_id)
        normalized = (query or "").strip()
        if normalized:
            pattern = f"%{normalized}%"
            statement = statement.where(
                or_(
                    RentalAssetRecord.asset_number.ilike(pattern),
                    CatalogProduct.title.ilike(pattern),
                    CatalogVariant.title.ilike(pattern),
                    CatalogVariant.sku.ilike(pattern),
                )
            )
        records = self._session.execute(statement).all()
        asset_ids = [record.id for record, _, _ in records]
        lost_ids = set(
            self._session.scalars(
                select(RentalOrderItemRecord.rental_asset_id).where(
                    RentalOrderItemRecord.rental_asset_id.in_(asset_ids),
                    RentalOrderItemRecord.status == RentalOrderItemStatus.LOST,
                )
            ).all()
        ) if asset_ids else set()
        current_orders = self._current_orders(asset_ids)
        rows = [
            RentalAssetOperationsRead(
                id=record.id,
                asset_number=record.asset_number,
                product_id=product.id,
                product_title=product.title,
                variant_id=variant.id,
                variant_title=variant.title,
                sku=variant.sku,
                condition=record.condition,
                availability=record.availability,
                is_lost=record.id in lost_ids,
                current_order_id=current_orders.get(record.id, (None, None, None))[0],
                current_order_number=current_orders.get(record.id, (None, None, None))[1],
                current_order_status=current_orders.get(record.id, (None, None, None))[2],
            )
            for record, variant, product in records
        ]
        if asset_filter is RentalAssetOperationsFilter.LOST:
            rows = [row for row in rows if row.is_lost]
        elif asset_filter is not RentalAssetOperationsFilter.ALL:
            rows = [row for row in rows if row.availability.value == asset_filter.value]
        if sort is RentalAssetOperationsSort.PRODUCT:
            return sorted(
                rows,
                key=lambda row: (row.product_title, row.variant_title, row.asset_number),
            )
        if sort is RentalAssetOperationsSort.STATUS:
            return sorted(rows, key=lambda row: (row.availability.value, row.asset_number))
        return sorted(rows, key=lambda row: row.asset_number)

    def _variants_for_products(self, product_ids: list[UUIDv7]) -> list[CatalogVariant]:
        if not product_ids:
            return []
        return list(
            self._session.scalars(
                select(CatalogVariant)
                .where(
                    CatalogVariant.product_id.in_(product_ids),
                    CatalogVariant.deleted_at.is_(None),
                )
                .order_by(CatalogVariant.title, CatalogVariant.sku)
            ).all()
        )

    def _asset_records_for_products(self, product_ids: list[UUIDv7]) -> list[RentalAssetRecord]:
        if not product_ids:
            return []
        return list(
            self._session.scalars(
                select(RentalAssetRecord)
                .join(CatalogVariant, CatalogVariant.id == RentalAssetRecord.variant_id)
                .where(
                    CatalogVariant.product_id.in_(product_ids),
                    RentalAssetRecord.deleted_at.is_(None),
                )
            ).all()
        )

    def _current_orders(
        self,
        asset_ids: list[UUIDv7],
    ) -> dict[UUIDv7, tuple[UUIDv7, str, RentalOrderStatus]]:
        if not asset_ids:
            return {}
        rows = self._session.execute(
            select(
                RentalOrderItemRecord.rental_asset_id,
                RentalOrderRecord.id,
                RentalOrderRecord.order_number,
                RentalOrderRecord.status,
            )
            .join(RentalOrderRecord, RentalOrderRecord.id == RentalOrderItemRecord.order_id)
            .where(
                RentalOrderItemRecord.rental_asset_id.in_(asset_ids),
                RentalOrderItemRecord.status == RentalOrderItemStatus.ISSUED,
                RentalOrderRecord.status == RentalOrderStatus.ISSUED,
            )
        ).all()
        return {
            asset_id: (order_id, order_number, order_status)
            for asset_id, order_id, order_number, order_status in rows
        }

    def _ever_priced_variant_ids(self, variant_ids: list[UUIDv7]) -> set[UUIDv7]:
        """Return variants that have at least one immutable retail price fact."""
        if not variant_ids:
            return set()
        return set(
            self._session.scalars(
                select(Price.variant_id).where(
                    Price.variant_id.in_(variant_ids),
                    Price.price_type == PriceType.RETAIL,
                ).distinct()
            ).all()
        )

    def _primary_images(
        self,
        products: list[CatalogProduct],
        variants: list[CatalogVariant],
    ) -> dict[tuple[ImageLinkEntityType, UUIDv7], UUIDv7]:
        """Return primary catalog image ids for operational cards."""
        product_ids = [product.id for product in products]
        variant_ids = [variant.id for variant in variants]
        if not product_ids and not variant_ids:
            return {}
        links = self._session.scalars(
            select(ImageLink).where(
                ImageLink.deleted_at.is_(None),
                ImageLink.role == ImageLinkRole.PRIMARY,
                or_(
                    ImageLink.entity_type == ImageLinkEntityType.CATALOG_PRODUCT,
                    ImageLink.entity_type == ImageLinkEntityType.CATALOG_VARIANT,
                ),
                ImageLink.entity_id.in_(product_ids + variant_ids),
            )
        ).all()
        return {(link.entity_type, link.entity_id): link.image_id for link in links}

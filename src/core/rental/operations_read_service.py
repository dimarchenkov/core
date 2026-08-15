from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.inventory.service import InventoryService
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.repository import PriceRepository
from core.rental.economics_schemas import EfficiencyFlag
from core.rental.economics_service import RentalEconomicsService
from core.rental.enums import AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.operations_schemas import (
    CatalogOperationsFilter,
    CatalogOperationsSort,
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
        sort: CatalogOperationsSort = CatalogOperationsSort.TITLE,
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
        economics_service = RentalEconomicsService(self._session)
        asset_economics = economics_service.get_assets([asset.id for asset in assets])
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
                category_id=product.category_id,
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
                economics=economics_service.get_product(product.id),
                last_rental_at=max(
                    (
                        asset_economics[asset.id].last_rental_at
                        for asset in assets_by_product[product.id]
                        if asset_economics[asset.id].last_rental_at is not None
                    ),
                    default=None,
                ),
                efficiency_flags=sorted(
                    {
                        flag
                        for asset in assets_by_product[product.id]
                        for flag in asset_economics[asset.id].flags
                    },
                    key=lambda flag: flag.value,
                ),
            )
            for product in products
        ]
        if product_filter is CatalogOperationsFilter.RENTAL:
            rows = [row for row in rows if row.rental_asset_count > 0]
        elif product_filter is CatalogOperationsFilter.AVAILABLE:
            rows = [row for row in rows if row.available_asset_count > 0]
        elif product_filter is CatalogOperationsFilter.NEEDS_PRICE:
            rows = [row for row in rows if row.needs_initial_price]
        efficiency_filters = {
            CatalogOperationsFilter.NEVER_RENTED: EfficiencyFlag.NEVER_RENTED,
            CatalogOperationsFilter.PAID_BACK: EfficiencyFlag.PAID_BACK,
            CatalogOperationsFilter.HIGH_EXPENSES: EfficiencyFlag.HIGH_EXPENSES,
            CatalogOperationsFilter.LONG_IDLE: EfficiencyFlag.LONG_IDLE,
        }
        if flag := efficiency_filters.get(product_filter):
            rows = [row for row in rows if flag in row.efficiency_flags]
        if sort is CatalogOperationsSort.REVENUE:
            return sorted(rows, key=lambda row: row.economics.revenue, reverse=True)
        if sort is CatalogOperationsSort.RENTAL_COUNT:
            return sorted(rows, key=lambda row: row.economics.rental_count, reverse=True)
        if sort is CatalogOperationsSort.PROFIT:
            return sorted(rows, key=lambda row: row.economics.profit, reverse=True)
        if sort is CatalogOperationsSort.LAST_RENTAL:
            return sorted(
                rows,
                key=lambda row: row.last_rental_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
        return sorted(rows, key=lambda row: row.title)

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
        balances = InventoryService(self._session).get_balances(variant_ids)
        reserved_rows = self._session.execute(
            select(RentalAssetRecord.variant_id, func.count(RentalAssetRecord.id))
            .where(
                RentalAssetRecord.variant_id.in_(variant_ids),
                RentalAssetRecord.purpose != AssetPurpose.SALE,
                RentalAssetRecord.deleted_at.is_(None),
            )
            .group_by(RentalAssetRecord.variant_id)
        )
        reserved_counts = {variant_id: count for variant_id, count in reserved_rows}
        priced_variant_ids = self._ever_priced_variant_ids(variant_ids)
        prices = PriceRepository(self._session)
        now = datetime.now(UTC)
        assets = self.list_assets(product_id=product_id)
        economics_service = RentalEconomicsService(self._session)
        assets_by_variant: dict[UUIDv7, list[RentalAssetOperationsRead]] = defaultdict(list)
        for asset in assets:
            assets_by_variant[asset.variant_id].append(asset)
        return CatalogProductOperationsDetail(
            id=product.id,
            title=product.title,
            description=product.description,
            category_id=product.category_id,
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
            needs_initial_price=any(variant.id not in priced_variant_ids for variant in variants),
            economics=economics_service.get_product(product.id),
            last_rental_at=max(
                (
                    asset.economics.last_rental_at
                    for asset in assets
                    if asset.economics.last_rental_at
                ),
                default=None,
            ),
            efficiency_flags=sorted(
                {flag for asset in assets for flag in asset.economics.flags},
                key=lambda flag: flag.value,
            ),
            variants=[
                CatalogVariantOperationsRead(
                    id=variant.id,
                    title=variant.title,
                    sku=variant.sku,
                    barcode=variant.barcode,
                    attributes=variant.attributes,
                    is_active=variant.is_active,
                    physical_quantity=balances[variant.id],
                    ordinary_quantity=(balances[variant.id] - reserved_counts.get(variant.id, 0)),
                    rental_asset_count=len(assets_by_variant[variant.id]),
                    available_asset_count=sum(
                        asset.availability is RentalAvailability.AVAILABLE
                        for asset in assets_by_variant[variant.id]
                    ),
                    rented_asset_count=sum(
                        asset.availability is RentalAvailability.RENTED
                        for asset in assets_by_variant[variant.id]
                    ),
                    primary_image_id=(
                        image_ids.get((ImageLinkEntityType.CATALOG_VARIANT, variant.id))
                        or image_ids.get((ImageLinkEntityType.CATALOG_PRODUCT, product.id))
                    ),
                    current_retail_price=(
                        current_price.amount
                        if (
                            current_price := prices.get_current(
                                variant.id, PriceType.RETAIL, at=now
                            )
                        )
                        is not None
                        else None
                    ),
                    retail_currency=(current_price.currency if current_price is not None else None),
                    current_rental_price=(
                        rental_price.amount
                        if (
                            rental_price := prices.get_current(variant.id, PriceType.RENTAL, at=now)
                        )
                        is not None
                        else None
                    ),
                    current_recommended_deposit=(
                        deposit.amount
                        if (
                            deposit := prices.get_current(
                                variant.id, PriceType.RENTAL_DEPOSIT, at=now
                            )
                        )
                        is not None
                        else None
                    ),
                    has_ever_retail_price=variant.id in priced_variant_ids,
                    economics=economics_service.get_variant(variant.id),
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
            .where(
                RentalAssetRecord.deleted_at.is_(None),
                RentalAssetRecord.purpose == AssetPurpose.RENTAL,
            )
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
        lost_ids = (
            set(
                self._session.scalars(
                    select(RentalOrderItemRecord.rental_asset_id).where(
                        RentalOrderItemRecord.rental_asset_id.in_(asset_ids),
                        RentalOrderItemRecord.status == RentalOrderItemStatus.LOST,
                    )
                ).all()
            )
            if asset_ids
            else set()
        )
        current_orders = self._current_orders(asset_ids)
        economics = RentalEconomicsService(self._session).get_assets(asset_ids)
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
                economics=economics[record.id],
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
        if sort is RentalAssetOperationsSort.REVENUE:
            return sorted(rows, key=lambda row: row.economics.revenue, reverse=True)
        if sort is RentalAssetOperationsSort.RENTAL_COUNT:
            return sorted(rows, key=lambda row: row.economics.rental_count, reverse=True)
        if sort is RentalAssetOperationsSort.PROFIT:
            return sorted(rows, key=lambda row: row.economics.net_income, reverse=True)
        if sort is RentalAssetOperationsSort.LAST_RENTAL:
            return sorted(
                rows,
                key=lambda row: row.economics.last_rental_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
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
                    RentalAssetRecord.purpose == AssetPurpose.RENTAL,
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
                select(Price.variant_id)
                .where(
                    Price.variant_id.in_(variant_ids),
                    Price.price_type == PriceType.RETAIL,
                )
                .distinct()
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

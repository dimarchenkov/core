from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.integrations.aqsi.enums import PublicationChannel, PublicationStatus
from core.integrations.aqsi.models import Publication
from core.inventory.service import InventoryService
from core.labels.renderer import meaningful_variant_name
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.repository import PriceRepository
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.rental.economics_schemas import EfficiencyFlag
from core.rental.economics_service import RentalEconomicsService
from core.rental.enums import AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.operations_schemas import (
    CatalogAttentionFilter,
    CatalogMode,
    CatalogOperationsFilter,
    CatalogOperationsSort,
    CatalogProductOperationsDetail,
    CatalogProductOperationsRead,
    CatalogStatus,
    CatalogVariantCardRead,
    CatalogVariantOperationsRead,
    RentalAssetOperationsFilter,
    RentalAssetOperationsRead,
    RentalAssetOperationsSort,
)
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.shared.db import UUIDv7
from core.supplier.models import Supplier


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
        mode: CatalogMode = CatalogMode.SALE,
        catalog_status: CatalogStatus = CatalogStatus.ACTIVE,
        category_id: UUIDv7 | None = None,
        supplier_id: UUIDv7 | None = None,
        attention_filters: frozenset[CatalogAttentionFilter] = frozenset(),
        product_filter: CatalogOperationsFilter = CatalogOperationsFilter.ALL,
        sort: CatalogOperationsSort = CatalogOperationsSort.TITLE,
    ) -> list[CatalogProductOperationsRead]:
        """Return shared Catalog products using server-side workspace filters."""
        statement = select(CatalogProduct)
        if catalog_status is CatalogStatus.ACTIVE:
            statement = statement.where(CatalogProduct.deleted_at.is_(None))
        elif catalog_status is CatalogStatus.ARCHIVED:
            statement = statement.where(
                or_(
                    CatalogProduct.deleted_at.is_not(None),
                    CatalogProduct.variants.any(CatalogVariant.deleted_at.is_not(None)),
                )
            )
        if category_id is not None:
            category_ids = self._category_with_descendants(category_id)
            statement = statement.where(
                CatalogProduct.category_id.in_(category_ids) if category_ids else false()
            )
        if supplier_id is not None:
            supplier_product_ids = self._posted_supplier_product_ids(supplier_id)
            statement = statement.where(
                CatalogProduct.id.in_(supplier_product_ids) if supplier_product_ids else false()
            )
        normalized = (query or "").strip()
        if normalized:
            pattern = f"%{normalized}%"
            variant_matches = or_(
                CatalogVariant.title.ilike(pattern),
                CatalogVariant.sku.ilike(pattern),
                CatalogVariant.barcode.ilike(pattern),
            )
            if catalog_status is CatalogStatus.ACTIVE:
                search_matches = or_(
                    CatalogProduct.title.ilike(pattern),
                    CatalogProduct.variants.any(
                        and_(CatalogVariant.deleted_at.is_(None), variant_matches)
                    ),
                )
            elif catalog_status is CatalogStatus.ARCHIVED:
                search_matches = or_(
                    CatalogProduct.title.ilike(pattern),
                    and_(
                        CatalogProduct.deleted_at.is_not(None),
                        CatalogProduct.variants.any(variant_matches),
                    ),
                    CatalogProduct.variants.any(
                        and_(CatalogVariant.deleted_at.is_not(None), variant_matches)
                    ),
                )
            else:
                search_matches = or_(
                    CatalogProduct.title.ilike(pattern),
                    CatalogProduct.variants.any(variant_matches),
                )
            statement = statement.where(search_matches)
        products = self._session.scalars(statement.order_by(CatalogProduct.title)).all()
        product_ids = [product.id for product in products]
        all_variants = self._variants_for_products(product_ids)
        products_by_id = {product.id: product for product in products}
        variants = [
            variant
            for variant in all_variants
            if variant.is_active
            and self._variant_matches_status(
                variant,
                product=products_by_id[variant.product_id],
                catalog_status=catalog_status,
            )
        ]
        visible_variant_ids = {variant.id for variant in variants}
        assets = [
            asset
            for asset in self._asset_records_for_products(product_ids)
            if asset.variant_id in visible_variant_ids
        ]
        variant_ids = [variant.id for variant in variants]
        image_ids = self._primary_images(products, variants)
        priced_variant_ids = self._ever_priced_variant_ids(variant_ids)
        current_prices = self._current_prices(variant_ids)
        balances = InventoryService(self._session).get_balances(variant_ids)
        reserved_counts = self._reserved_asset_counts(variant_ids)
        publications = self._aqsi_publications(variant_ids)
        category_labels = self._category_labels([product.category_id for product in products])
        current_priced_variant_ids = {
            variant_id
            for variant_id, price_type in current_prices
            if price_type is PriceType.RETAIL
        }
        aqsi_problem_variant_ids = {
            variant.id
            for variant in variants
            if not self._aqsi_is_current(publications.get(variant.id))
        }
        economics_service = RentalEconomicsService(self._session)
        asset_economics = economics_service.get_assets([asset.id for asset in assets])
        variants_by_product: dict[UUIDv7, list[CatalogVariant]] = defaultdict(list)
        assets_by_product: dict[UUIDv7, list[RentalAssetRecord]] = defaultdict(list)
        assets_by_variant: dict[UUIDv7, list[RentalAssetRecord]] = defaultdict(list)
        for variant in variants:
            variants_by_product[variant.product_id].append(variant)
        variant_products = {variant.id: variant.product_id for variant in all_variants}
        for asset in assets:
            assets_by_product[variant_products[asset.variant_id]].append(asset)
            assets_by_variant[asset.variant_id].append(asset)

        rows = [
            CatalogProductOperationsRead(
                id=product.id,
                title=product.title,
                description=product.description,
                category_id=product.category_id,
                category_label=category_labels.get(product.category_id, "Без категории"),
                is_active=product.is_active,
                is_archived=product.deleted_at is not None,
                is_test=product.is_test,
                skus=[variant.sku for variant in variants_by_product[product.id]],
                variant_count=len(variants_by_product[product.id]),
                card_variants=[
                    self._variant_card(
                        variant,
                        current_prices=current_prices,
                        balances=balances,
                        publications=publications,
                        rental_asset_count=len(assets_by_variant[variant.id]),
                        reserved_asset_count=reserved_counts.get(variant.id, 0),
                    )
                    for variant in variants_by_product[product.id]
                ],
                rental_asset_count=len(assets_by_product[product.id]),
                rental_economics_applicable=bool(assets_by_product[product.id]),
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
                economics=economics_service.summarize_product(
                    product.id,
                    [asset_economics[asset.id] for asset in assets_by_product[product.id]],
                ),
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
        if mode is CatalogMode.SALE:
            rows = [row for row in rows if row.is_active and row.variant_count > 0]
        if mode is CatalogMode.RENTAL:
            rows = [row for row in rows if row.rental_asset_count > 0]
        if CatalogAttentionFilter.MISSING_PRICE in attention_filters:
            rows = [
                row
                for row in rows
                if any(
                    variant.id not in current_priced_variant_ids
                    for variant in variants_by_product[row.id]
                    if variant.is_active
                )
            ]
        if CatalogAttentionFilter.MISSING_PHOTO in attention_filters:
            rows = [row for row in rows if row.primary_image_id is None]
        if CatalogAttentionFilter.AQSI_PROBLEM in attention_filters:
            rows = [
                row
                for row in rows
                if any(
                    variant.id in aqsi_problem_variant_ids
                    for variant in variants_by_product[row.id]
                    if variant.is_active
                )
            ]
        if CatalogAttentionFilter.OUT_OF_STOCK in attention_filters:
            rows = [
                row
                for row in rows
                if sum(
                    balances.get(variant.id, Decimal("0"))
                    for variant in variants_by_product[row.id]
                    if variant.is_active
                )
                - Decimal(row.rental_asset_count)
                <= 0
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

    def _category_with_descendants(self, category_id: UUIDv7) -> set[UUIDv7]:
        """Return one live Category and its live descendants without assuming tree depth."""
        categories = list(
            self._session.scalars(select(Category).where(Category.deleted_at.is_(None))).all()
        )
        if not any(category.id == category_id for category in categories):
            return set()
        descendants = {category_id}
        changed = True
        while changed:
            changed = False
            for category in categories:
                if category.parent_id in descendants and category.id not in descendants:
                    descendants.add(category.id)
                    changed = True
        return descendants

    def _posted_supplier_product_ids(self, supplier_id: UUIDv7) -> set[UUIDv7]:
        """Return Products sourced through posted Receipt history from one Supplier."""
        supplier_exists = self._session.scalar(
            select(Supplier.id).where(
                Supplier.id == supplier_id,
                Supplier.deleted_at.is_(None),
            )
        )
        if supplier_exists is None:
            return set()
        return set(
            self._session.scalars(
                select(CatalogVariant.product_id)
                .join(ReceiptItem, ReceiptItem.variant_id == CatalogVariant.id)
                .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
                .where(
                    Receipt.supplier_id == supplier_id,
                    Receipt.status == ReceiptStatus.POSTED,
                    Receipt.deleted_at.is_(None),
                    ReceiptItem.deleted_at.is_(None),
                )
                .distinct()
            ).all()
        )

    def _current_prices(
        self,
        variant_ids: list[UUIDv7],
    ) -> dict[tuple[UUIDv7, PriceType], Price]:
        """Return current retail and rental Prices for each Variant in one query."""
        if not variant_ids:
            return {}
        prices = self._session.scalars(
            select(Price)
            .where(
                Price.variant_id.in_(variant_ids),
                Price.price_type.in_((PriceType.RETAIL, PriceType.RENTAL)),
                Price.effective_from <= datetime.now(UTC),
            )
            .order_by(
                Price.variant_id,
                Price.price_type,
                Price.effective_from.desc(),
                Price.created_at.desc(),
                Price.id.desc(),
            )
        ).all()
        current: dict[tuple[UUIDv7, PriceType], Price] = {}
        for price in prices:
            current.setdefault((price.variant_id, price.price_type), price)
        return current

    def _aqsi_publications(self, variant_ids: list[UUIDv7]) -> dict[UUIDv7, Publication]:
        """Return persisted AQSI publication rows for the requested Variants."""
        if not variant_ids:
            return {}
        rows = self._session.scalars(
            select(Publication).where(
                Publication.variant_id.in_(variant_ids),
                Publication.channel == PublicationChannel.AQSI,
                Publication.deleted_at.is_(None),
            )
        ).all()
        return {publication.variant_id: publication for publication in rows}

    def _reserved_asset_counts(self, variant_ids: list[UUIDv7]) -> dict[UUIDv7, int]:
        """Count tracked units unavailable to ordinary Sale using existing detail semantics."""
        if not variant_ids:
            return {}
        rows = self._session.execute(
            select(RentalAssetRecord.variant_id, func.count(RentalAssetRecord.id))
            .where(
                RentalAssetRecord.variant_id.in_(variant_ids),
                RentalAssetRecord.purpose != AssetPurpose.SALE,
                RentalAssetRecord.deleted_at.is_(None),
            )
            .group_by(RentalAssetRecord.variant_id)
        )
        return {variant_id: count for variant_id, count in rows}

    @staticmethod
    def _aqsi_is_current(publication: Publication | None) -> bool:
        """Return whether local evidence says AQSI has the requested payload."""
        return bool(
            publication is not None
            and publication.status is PublicationStatus.PUBLISHED
            and publication.last_requested_payload_hash is not None
            and publication.last_requested_payload_hash == publication.last_verified_payload_hash
        )

    def _variant_card(
        self,
        variant: CatalogVariant,
        *,
        current_prices: dict[tuple[UUIDv7, PriceType], Price],
        balances: dict[UUIDv7, Decimal],
        publications: dict[UUIDv7, Publication],
        rental_asset_count: int,
        reserved_asset_count: int,
    ) -> CatalogVariantCardRead:
        """Build one list-card Variant from already-loaded application facts."""
        retail_price = current_prices.get((variant.id, PriceType.RETAIL))
        rental_price = current_prices.get((variant.id, PriceType.RENTAL))
        publication = publications.get(variant.id)
        stock_balance = balances.get(variant.id, Decimal("0"))
        sale_quantity = stock_balance - Decimal(reserved_asset_count)
        sale_row_visible = bool(
            reserved_asset_count == 0
            or sale_quantity != 0
            or retail_price is not None
            or publication is not None
        )
        return CatalogVariantCardRead(
            id=variant.id,
            title=meaningful_variant_name(variant.title) or None,
            sku=variant.sku,
            is_archived=variant.deleted_at is not None,
            current_retail_price=retail_price.amount if retail_price is not None else None,
            retail_currency=retail_price.currency if retail_price is not None else None,
            current_rental_price=rental_price.amount if rental_price is not None else None,
            rental_currency=rental_price.currency if rental_price is not None else None,
            stock_balance=stock_balance,
            sale_quantity=sale_quantity,
            rental_asset_count=rental_asset_count,
            sale_row_visible=sale_row_visible,
            rental_row_visible=rental_asset_count > 0,
            aqsi_status=publication.status if publication is not None else None,
            aqsi_is_current=self._aqsi_is_current(publication),
        )

    @staticmethod
    def _variant_matches_status(
        variant: CatalogVariant,
        *,
        product: CatalogProduct,
        catalog_status: CatalogStatus,
    ) -> bool:
        """Select card Variants without mixing live and archived child state."""
        if catalog_status is CatalogStatus.ACTIVE:
            return product.deleted_at is None and variant.deleted_at is None
        if catalog_status is CatalogStatus.ARCHIVED:
            return product.deleted_at is not None or variant.deleted_at is not None
        return True

    def _category_labels(self, category_ids: list[UUIDv7]) -> dict[UUIDv7, str]:
        """Build bounded readable Category paths without relationship N+1 queries."""
        if not category_ids:
            return {}
        categories = list(
            self._session.scalars(select(Category).where(Category.deleted_at.is_(None))).all()
        )
        by_id = {category.id: category for category in categories}
        labels: dict[UUIDv7, str] = {}
        for category_id in set(category_ids):
            path: list[str] = []
            visited: set[UUIDv7] = set()
            current = by_id.get(category_id)
            while current is not None and current.id not in visited:
                visited.add(current.id)
                path.append(current.title)
                current = by_id.get(current.parent_id) if current.parent_id else None
            path.reverse()
            if len(path) > 3:
                path = ["…", *path[-2:]]
            labels[category_id] = " › ".join(path)
        return labels

    def get_product(self, product_id: UUIDv7) -> CatalogProductOperationsDetail:
        """Return one product with all variants and RentalAssets."""
        product = self._session.scalar(
            select(CatalogProduct).where(CatalogProduct.id == product_id)
        )
        if product is None:
            raise OperationsProductNotFoundError
        variants = self._variants_for_products([product_id])
        image_ids = self._primary_images([product], variants)
        variant_ids = [variant.id for variant in variants]
        balances = InventoryService(self._session).get_balances(variant_ids)
        current_card_prices = self._current_prices(variant_ids)
        publications = self._aqsi_publications(variant_ids)
        reserved_counts = self._reserved_asset_counts(variant_ids)
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
            category_label=self._category_labels([product.category_id]).get(
                product.category_id, "Без категории"
            ),
            is_active=product.is_active,
            is_archived=product.deleted_at is not None,
            is_test=product.is_test,
            skus=[variant.sku for variant in variants],
            variant_count=len(variants),
            card_variants=[
                self._variant_card(
                    variant,
                    current_prices=current_card_prices,
                    balances=balances,
                    publications=publications,
                    rental_asset_count=len(assets_by_variant[variant.id]),
                    reserved_asset_count=reserved_counts.get(variant.id, 0),
                )
                for variant in variants
                if variant.is_active
            ],
            rental_asset_count=len(assets),
            rental_economics_applicable=bool(assets),
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
                    barcode_source=variant.barcode_source,
                    attributes=variant.attributes,
                    is_active=variant.is_active,
                    is_archived=variant.deleted_at is not None,
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
                        current_card_prices[(variant.id, PriceType.RETAIL)].amount
                        if (variant.id, PriceType.RETAIL) in current_card_prices
                        else None
                    ),
                    retail_currency=(
                        current_card_prices[(variant.id, PriceType.RETAIL)].currency
                        if (variant.id, PriceType.RETAIL) in current_card_prices
                        else None
                    ),
                    current_rental_price=(
                        current_card_prices[(variant.id, PriceType.RENTAL)].amount
                        if (variant.id, PriceType.RENTAL) in current_card_prices
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
                    CatalogVariant.barcode.ilike(pattern),
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
                .where(CatalogVariant.product_id.in_(product_ids))
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

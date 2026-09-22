from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.catalog.schemas import (
    CatalogProductSearchPage,
    CatalogProductSearchResult,
    CatalogVariantSearchPage,
    CatalogVariantSearchResult,
)
from core.pricing.enums import PriceType
from core.pricing.models import Price


class CatalogSearchService:
    """Shared server-side Catalog search with workflow-specific projections."""

    def __init__(self, session: Session) -> None:
        """Bind the read service to a request-scoped database session."""
        self._session = session

    def search_variants(self, query: str, *, limit: int) -> CatalogVariantSearchPage:
        """Find active Variants by current operational identifiers and display text."""
        normalized, terms = self._normalize(query)
        if not normalized:
            return CatalogVariantSearchPage(items=[], has_more=False)
        if self._is_sqlite:
            matches = self._python_variant_matches(normalized, terms)
            return CatalogVariantSearchPage(
                items=[
                    self._variant_result(variant, product)
                    for variant, product in matches[:limit]
                ],
                has_more=len(matches) > limit,
            )

        statement = self._variant_statement(normalized, terms).limit(limit + 1)
        rows = self._session.execute(statement).all()
        return CatalogVariantSearchPage(
            items=[self._variant_result(variant, product) for variant, product in rows[:limit]],
            has_more=len(rows) > limit,
        )

    def search_products(self, query: str, *, limit: int) -> CatalogProductSearchPage:
        """Find and deduplicate parent Products even when a child Variant matched."""
        normalized, terms = self._normalize(query)
        if not normalized:
            return CatalogProductSearchPage(items=[], has_more=False)
        if self._is_sqlite:
            matches = self._python_variant_matches(normalized, terms)
            direct_products = [
                product
                for product in self._active_products()
                if all(term in product.title.casefold() for term in terms)
            ]
        else:
            matches = self._session.execute(
                self._variant_statement(normalized, terms).limit((limit + 1) * 25)
            ).all()
            direct_products = list(
                self._session.scalars(
                    select(CatalogProduct)
                    .where(
                        CatalogProduct.deleted_at.is_(None),
                        CatalogProduct.is_active.is_(True),
                        and_(*(CatalogProduct.title.ilike(f"%{term}%") for term in terms)),
                    )
                    .order_by(CatalogProduct.title, CatalogProduct.id)
                    .limit(limit + 1)
                )
            )

        by_product: dict[object, tuple[CatalogProduct, CatalogVariant | None]] = {}
        for variant, product in matches:
            by_product.setdefault(product.id, (product, variant))
        for product in direct_products:
            by_product.setdefault(product.id, (product, None))
        product_matches = list(by_product.values())
        items = [
            self._product_result(product, variant, normalized)
            for product, variant in product_matches[:limit]
        ]
        return CatalogProductSearchPage(items=items, has_more=len(product_matches) > limit)

    @property
    def _is_sqlite(self) -> bool:
        return bool(self._session.bind and self._session.bind.dialect.name == "sqlite")

    @staticmethod
    def _normalize(query: str) -> tuple[str, list[str]]:
        normalized = " ".join(query.strip().split()).casefold()
        return normalized, normalized.split()

    @staticmethod
    def _search_text() -> object:
        return func.concat_ws(
            " ",
            CatalogProduct.title,
            CatalogVariant.title,
            CatalogVariant.sku,
            CatalogVariant.barcode,
        )

    def _variant_statement(
        self, normalized: str, terms: Iterable[str]
    ) -> Select[tuple[CatalogVariant, CatalogProduct]]:
        text = self._search_text()
        filters = [text.ilike(f"%{term}%") for term in terms]
        return (
            select(CatalogVariant, CatalogProduct)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
                CatalogProduct.deleted_at.is_(None),
                CatalogProduct.is_active.is_(True),
                and_(*filters),
            )
            .order_by(
                case((func.lower(CatalogVariant.barcode) == normalized, 0), else_=1),
                case((func.lower(CatalogVariant.sku) == normalized, 0), else_=1),
                CatalogProduct.title,
                CatalogVariant.title,
                CatalogVariant.id,
            )
        )

    def _python_variant_matches(
        self, normalized: str, terms: Iterable[str]
    ) -> list[tuple[CatalogVariant, CatalogProduct]]:
        rows = self._session.execute(
            select(CatalogVariant, CatalogProduct)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
                CatalogProduct.deleted_at.is_(None),
                CatalogProduct.is_active.is_(True),
            )
        ).all()
        found = [
            (variant, product)
            for variant, product in rows
            if all(
                term
                in " ".join(
                    (product.title, variant.title, variant.sku, variant.barcode)
                ).casefold()
                for term in terms
            )
        ]
        return sorted(
            found,
            key=lambda row: (
                row[0].barcode.casefold() != normalized,
                row[0].sku.casefold() != normalized,
                row[1].title.casefold(),
                row[0].title.casefold(),
                str(row[0].id),
            ),
        )

    def _active_products(self) -> list[CatalogProduct]:
        return list(
            self._session.scalars(
                select(CatalogProduct).where(
                    CatalogProduct.deleted_at.is_(None), CatalogProduct.is_active.is_(True)
                )
            )
        )

    def _variant_result(
        self, variant: CatalogVariant, product: CatalogProduct
    ) -> CatalogVariantSearchResult:
        current_price = self._session.scalar(
            select(Price.amount)
            .where(
                Price.variant_id == variant.id,
                Price.price_type == PriceType.RETAIL,
                Price.effective_from <= datetime.now(UTC),
            )
            .order_by(Price.effective_from.desc(), Price.created_at.desc(), Price.id.desc())
            .limit(1)
        )
        return CatalogVariantSearchResult(
            id=variant.id,
            product_id=product.id,
            product_title=product.title,
            title=variant.title,
            sku=variant.sku,
            barcode=variant.barcode,
            retail_price=Decimal(current_price) if current_price is not None else None,
        )

    def _product_result(
        self, product: CatalogProduct, matched: CatalogVariant | None, normalized: str
    ) -> CatalogProductSearchResult:
        count = self._session.scalar(
            select(func.count(CatalogVariant.id)).where(
                CatalogVariant.product_id == product.id,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
            )
        )
        direct_product_match = matched is None or all(
            term in product.title.casefold() for term in normalized.split()
        )
        return CatalogProductSearchResult(
            id=product.id,
            title=product.title,
            variant_count=int(count or 0),
            matched_variant_title=None if direct_product_match else matched.title,
            matched_sku=None if direct_product_match else matched.sku,
            matched_barcode=None if direct_product_match else matched.barcode,
        )

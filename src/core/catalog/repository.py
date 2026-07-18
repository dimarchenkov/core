from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.shared.db import UUIDv7


class CategoryRepository:
    """Database access for catalog categories."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, category: Category) -> Category:
        """Add a category to the current unit of work."""
        self._session.add(category)
        return category

    def get(self, category_id: UUIDv7) -> Category | None:
        """Return an active category by id, excluding soft-deleted rows."""
        statement = select(Category).where(
            Category.id == category_id,
            Category.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def get_by_slug(self, slug: str) -> Category | None:
        """Return an active category by slug, excluding soft-deleted rows."""
        statement = select(Category).where(
            Category.slug == slug,
            Category.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def list(self) -> Sequence[Category]:
        """Return categories ordered for catalog navigation."""
        statement = (
            select(Category)
            .where(Category.deleted_at.is_(None))
            .order_by(Category.sort_order, Category.title)
        )
        return self._session.scalars(statement).all()


class CatalogProductRepository:
    """Database access for catalog product families."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, product: CatalogProduct) -> CatalogProduct:
        """Add a catalog product to the current unit of work."""
        self._session.add(product)
        return product

    def get(self, product_id: UUIDv7) -> CatalogProduct | None:
        """Return a non-deleted catalog product by id."""
        statement = select(CatalogProduct).where(
            CatalogProduct.id == product_id,
            CatalogProduct.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def get_by_slug(self, slug: str) -> CatalogProduct | None:
        """Return a non-deleted catalog product by slug."""
        statement = select(CatalogProduct).where(
            CatalogProduct.slug == slug,
            CatalogProduct.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def list(self) -> Sequence[CatalogProduct]:
        """Return non-deleted catalog products ordered for display."""
        statement = (
            select(CatalogProduct)
            .where(CatalogProduct.deleted_at.is_(None))
            .order_by(CatalogProduct.title)
        )
        return self._session.scalars(statement).all()


class CatalogVariantRepository:
    """Database access for sellable catalog variants."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, variant: CatalogVariant) -> CatalogVariant:
        """Add a catalog variant to the current unit of work."""
        self._session.add(variant)
        return variant

    def get(self, variant_id: UUIDv7) -> CatalogVariant | None:
        """Return a non-deleted catalog variant by id."""
        statement = select(CatalogVariant).where(
            CatalogVariant.id == variant_id,
            CatalogVariant.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def get_for_update(self, variant_id: UUIDv7) -> CatalogVariant | None:
        """Return and lock one non-deleted Variant for a serialized workflow command."""
        statement = (
            select(CatalogVariant)
            .where(
                CatalogVariant.id == variant_id,
                CatalogVariant.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def list(self) -> Sequence[CatalogVariant]:
        """Return non-deleted catalog variants ordered for display."""
        statement = (
            select(CatalogVariant)
            .where(CatalogVariant.deleted_at.is_(None))
            .order_by(CatalogVariant.title)
        )
        return self._session.scalars(statement).all()

    def get_by_barcode(self, barcode: str) -> CatalogVariant | None:
        """Return a variant by its globally unique Core barcode."""
        statement = select(CatalogVariant).where(CatalogVariant.barcode == barcode)
        return self._session.scalar(statement)

    def get_active_by_barcode(self, barcode: str) -> CatalogVariant | None:
        """Return a non-archived variant by its exact barcode."""
        statement = select(CatalogVariant).where(
            CatalogVariant.barcode == barcode,
            CatalogVariant.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def next_sku_number(self) -> int:
        """Reserve the next SKU number from PostgreSQL or the test database."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            return self._session.scalar(text("SELECT nextval('catalog_variant_sku_seq')"))

        statement = select(CatalogVariant.sku).order_by(CatalogVariant.sku.desc()).limit(1)
        sku = self._session.scalar(statement)
        if sku is None:
            return 1
        return int(sku.removeprefix("SKU-")) + 1

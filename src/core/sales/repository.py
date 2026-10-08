from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from core.catalog.models import CatalogProduct, CatalogVariant
from core.sales.enums import SaleStatus
from core.sales.models import Fiscalization, PaymentAttempt, Sale, SaleItem
from core.shared.db import UUIDv7


class SaleRepository:
    """Database access for operator-owned Sales carts and their items."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to one transaction-scoped session."""
        self._session = session

    def add(self, sale: Sale) -> Sale:
        """Stage a new Sale in the current unit of work."""
        self._session.add(sale)
        return sale

    def add_item(self, item: SaleItem) -> SaleItem:
        """Stage a new SaleItem snapshot in the current unit of work."""
        self._session.add(item)
        return item

    def add_payment(self, attempt: PaymentAttempt) -> PaymentAttempt:
        """Stage one immutable-identity payment attempt."""
        self._session.add(attempt)
        return attempt

    def add_fiscalization(self, fiscalization: Fiscalization) -> Fiscalization:
        """Stage the single fiscal obligation owned by a paid Sale."""
        self._session.add(fiscalization)
        return fiscalization

    def next_sale_number(self) -> int:
        """Reserve the next human-readable Sale number."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            return self._session.scalar(text("SELECT nextval('sale_number_seq')"))
        return (self._session.scalar(select(func.max(Sale.sale_number))) or 0) + 1

    def list_drafts(self, owner_id: UUIDv7) -> Sequence[Sale]:
        """List one operator's draft carts newest first with items loaded."""
        statement = (
            select(Sale)
            .where(
                Sale.owner_id == owner_id,
                Sale.status == SaleStatus.DRAFT,
                Sale.deleted_at.is_(None),
            )
            .options(selectinload(Sale.items))
            .order_by(Sale.updated_at.desc(), Sale.sale_number.desc())
        )
        return self._session.scalars(statement).all()

    def get_owned(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale | None:
        """Return an owned Sale in any implemented lifecycle state."""
        statement = (
            select(Sale)
            .where(
                Sale.id == sale_id,
                Sale.owner_id == owner_id,
                Sale.deleted_at.is_(None),
            )
            .options(selectinload(Sale.items))
        )
        return self._session.scalar(statement)

    def get_owned_for_update(self, sale_id: UUIDv7, owner_id: UUIDv7) -> Sale | None:
        """Lock an owned Sale so cart mutations serialize on one aggregate row."""
        statement = (
            select(Sale)
            .where(
                Sale.id == sale_id,
                Sale.owner_id == owner_id,
                Sale.deleted_at.is_(None),
            )
            .options(selectinload(Sale.items))
            .with_for_update()
        )
        return self._session.scalar(statement)

    def get_operational_variant(self, variant_id: UUIDv7) -> CatalogVariant | None:
        """Return a Variant that can participate in a normal Sale."""
        statement = (
            select(CatalogVariant)
            .join(CatalogProduct, CatalogVariant.product_id == CatalogProduct.id)
            .where(
                CatalogVariant.id == variant_id,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
                CatalogProduct.deleted_at.is_(None),
                CatalogProduct.is_active.is_(True),
            )
            .options(selectinload(CatalogVariant.product))
        )
        return self._session.scalar(statement)

    def get_operational_variant_by_barcode(self, barcode: str) -> CatalogVariant | None:
        """Resolve the exact current canonical barcode of an operational Variant."""
        statement = (
            select(CatalogVariant)
            .join(CatalogProduct, CatalogVariant.product_id == CatalogProduct.id)
            .where(
                CatalogVariant.barcode == barcode,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
                CatalogProduct.deleted_at.is_(None),
                CatalogProduct.is_active.is_(True),
            )
            .options(selectinload(CatalogVariant.product))
        )
        return self._session.scalar(statement)

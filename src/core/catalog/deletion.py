from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode
from core.catalog.schemas import CatalogDeletionDependency, CatalogDeletionPreflight
from core.intake.enums import IntakeSessionStatus
from core.intake.models import IntakeItemDraft, IntakeSession
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.inventory.models import StockMovement
from core.media.enums import ImageLinkEntityType
from core.media.models import ImageLink
from core.pricing.models import Price
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.rental.models import RentalAssetRecord
from core.shared.db import UUIDv7


class CatalogHardDeleteNotFoundError(Exception):
    """Raised when the requested live catalog entity does not exist."""


class CatalogHardDeleteBlockedError(Exception):
    """Raised when protected business history prevents physical deletion."""

    def __init__(self, preflight: CatalogDeletionPreflight) -> None:
        """Retain the authoritative preflight for an HTTP conflict response."""
        super().__init__("Protected business history prevents hard deletion.")
        self.preflight = preflight


@dataclass(frozen=True)
class _DeleteScope:
    """Resolved Product and Variant identifiers covered by one delete command."""

    product_id: UUIDv7
    variant_ids: tuple[UUIDv7, ...]
    include_product: bool


class CatalogHardDeleteService:
    """Preflight and physically delete disposable Catalog data without business history."""

    def __init__(self, session: Session) -> None:
        """Bind the delete workflow to the route-owned transaction."""
        self._session = session

    def preflight_product(self, product_id: UUIDv7) -> CatalogDeletionPreflight:
        """Calculate the real local impact and blockers for a Product hard delete."""
        product = self._get_product(product_id)
        scope = self._product_scope(product.id)
        return self._build_preflight(
            scope,
            entity_type="product",
            entity_id=product.id,
            title=product.title,
        )

    def preflight_variant(self, variant_id: UUIDv7) -> CatalogDeletionPreflight:
        """Calculate the real local impact and blockers for a Variant hard delete."""
        variant = self._get_variant(variant_id)
        scope = _DeleteScope(
            product_id=variant.product_id,
            variant_ids=(variant.id,),
            include_product=False,
        )
        sibling_count = self._count(
            select(func.count(CatalogVariant.id)).where(
                CatalogVariant.product_id == variant.product_id,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.id != variant.id,
            )
        )
        return self._build_preflight(
            scope,
            entity_type="variant",
            entity_id=variant.id,
            title=variant.title,
            is_last_variant=sibling_count == 0,
        )

    def delete_product(self, product_id: UUIDv7) -> None:
        """Lock, recheck, and physically delete a Product and safe owned dependencies."""
        product = self._lock_product(product_id)
        scope = self._product_scope(product.id, lock=True)
        preflight = self._build_preflight(
            scope,
            entity_type="product",
            entity_id=product.id,
            title=product.title,
            lock_dependencies=True,
        )
        self._raise_if_blocked(preflight)
        self._delete_scope(scope, delete_product=True)

    def delete_variant(self, variant_id: UUIDv7) -> None:
        """Lock, recheck, and physically delete one Variant while retaining its Product."""
        variant = self._lock_variant(variant_id)
        scope = _DeleteScope(
            product_id=variant.product_id,
            variant_ids=(variant.id,),
            include_product=False,
        )
        preflight = self._build_preflight(
            scope,
            entity_type="variant",
            entity_id=variant.id,
            title=variant.title,
            lock_dependencies=True,
        )
        self._raise_if_blocked(preflight)
        self._delete_scope(scope, delete_product=False)

    def _build_preflight(
        self,
        scope: _DeleteScope,
        *,
        entity_type: str,
        entity_id: UUIDv7,
        title: str,
        is_last_variant: bool = False,
        lock_dependencies: bool = False,
    ) -> CatalogDeletionPreflight:
        """Build counts from current database facts without trusting the client."""
        intake_rows = self._intake_rows(scope, lock=lock_dependencies)
        receipt_rows = self._receipt_rows(scope, lock=lock_dependencies)
        draft_intake_count = sum(row.status is IntakeSessionStatus.DRAFT for row in intake_rows)
        protected_intake_count = len(intake_rows) - draft_intake_count
        draft_receipt_count = sum(row.status is ReceiptStatus.DRAFT for row in receipt_rows)
        protected_receipt_count = len(receipt_rows) - draft_receipt_count
        stock_count = self._stock_count(scope)
        rental_count = self._rental_count(scope)
        publication_count, attempt_count = self._publication_counts(scope)

        will_delete = [
            self._dependency("products", "товар", 1 if entity_type == "product" else 0),
            self._dependency("variants", "вариант", len(scope.variant_ids)),
            self._dependency("barcodes", "штрихкод", self._barcode_count(scope)),
            self._dependency("prices", "запись цены", self._price_count(scope)),
            self._dependency("image_links", "связь с фото", self._image_link_count(scope)),
            self._dependency("draft_intake_items", "позиция черновика приёмки", draft_intake_count),
            self._dependency(
                "draft_receipt_items",
                "позиция черновика прихода",
                draft_receipt_count,
            ),
            self._dependency("aqsi_publications", "локальная публикация AQSI", publication_count),
            self._dependency("aqsi_attempts", "локальная попытка публикации AQSI", attempt_count),
        ]
        blockers = [
            self._dependency(
                "protected_intake",
                "завершённая или закрытая приёмка",
                protected_intake_count,
            ),
            self._dependency(
                "protected_receipts",
                "проведённый или отменённый приход",
                protected_receipt_count,
            ),
            self._dependency("stock_movements", "складское движение", stock_count),
            self._dependency("rental_assets", "предмет аренды RentalAsset", rental_count),
        ]
        warnings = []
        if publication_count:
            warnings.append("Товар публиковался в AQSI. Локальное удаление не удалит его с кассы.")
        return CatalogDeletionPreflight(
            entity_type=entity_type,
            entity_id=entity_id,
            title=title,
            can_delete=not any(blockers),
            will_delete=[item for item in will_delete if item is not None],
            blockers=[item for item in blockers if item is not None],
            warnings=warnings,
            is_last_variant=is_last_variant,
            product_id=scope.product_id,
        )

    def _delete_scope(self, scope: _DeleteScope, *, delete_product: bool) -> None:
        """Delete safe dependencies in foreign-key order and flush as one transaction."""
        publication_ids = tuple(
            self._session.scalars(
                select(Publication.id).where(Publication.variant_id.in_(scope.variant_ids))
            ).all()
        )
        if publication_ids:
            self._session.execute(
                delete(PublicationAttempt).where(
                    PublicationAttempt.publication_id.in_(publication_ids)
                )
            )
        self._session.execute(
            delete(Publication).where(Publication.variant_id.in_(scope.variant_ids))
        )
        self._session.execute(delete(Price).where(Price.variant_id.in_(scope.variant_ids)))
        self._session.execute(
            delete(CatalogVariantBarcode).where(
                CatalogVariantBarcode.variant_id.in_(scope.variant_ids)
            )
        )
        self._delete_draft_references(scope)
        media_condition = self._media_condition(scope, include_product=delete_product)
        self._session.execute(delete(ImageLink).where(media_condition))
        self._session.execute(
            delete(CatalogVariant).where(CatalogVariant.id.in_(scope.variant_ids))
        )
        if delete_product:
            self._session.execute(
                delete(CatalogProduct).where(CatalogProduct.id == scope.product_id)
            )
        self._session.flush()

    def _delete_draft_references(self, scope: _DeleteScope) -> None:
        """Remove only mutable draft rows that point at the deleted Catalog scope."""
        intake_ids = tuple(
            row.id for row in self._intake_rows(scope) if row.status is IntakeSessionStatus.DRAFT
        )
        if intake_ids:
            self._session.execute(delete(IntakeItemDraft).where(IntakeItemDraft.id.in_(intake_ids)))
        receipt_ids = tuple(
            row.id for row in self._receipt_rows(scope) if row.status is ReceiptStatus.DRAFT
        )
        if receipt_ids:
            self._session.execute(delete(ReceiptItem).where(ReceiptItem.id.in_(receipt_ids)))

    def _intake_rows(self, scope: _DeleteScope, *, lock: bool = False) -> list[object]:
        """Return linked Intake rows paired with their owning session status."""
        statement = (
            select(IntakeItemDraft.id, IntakeSession.status)
            .join(IntakeSession, IntakeSession.id == IntakeItemDraft.session_id)
            .where(self._catalog_reference_condition(scope))
        )
        if lock:
            statement = statement.with_for_update()
        return list(self._session.execute(statement).all())

    def _receipt_rows(self, scope: _DeleteScope, *, lock: bool = False) -> list[object]:
        """Return linked receipt rows paired with their document status."""
        statement = (
            select(ReceiptItem.id, Receipt.status)
            .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
            .where(ReceiptItem.variant_id.in_(scope.variant_ids))
        )
        if lock:
            statement = statement.with_for_update()
        return list(self._session.execute(statement).all())

    def _catalog_reference_condition(self, scope: _DeleteScope) -> object:
        """Build the direct Intake reference predicate for a Catalog scope."""
        return or_(
            IntakeItemDraft.variant_id.in_(scope.variant_ids),
            (IntakeItemDraft.product_id == scope.product_id) if scope.include_product else False,
        )

    def _media_condition(self, scope: _DeleteScope, *, include_product: bool) -> object:
        """Build a polymorphic ImageLink predicate without touching Image metadata."""
        conditions = [
            (
                (ImageLink.entity_type == ImageLinkEntityType.CATALOG_VARIANT)
                & ImageLink.entity_id.in_(scope.variant_ids)
            )
        ]
        if include_product:
            conditions.append(
                (ImageLink.entity_type == ImageLinkEntityType.CATALOG_PRODUCT)
                & (ImageLink.entity_id == scope.product_id)
            )
        return or_(*conditions)

    def _product_scope(self, product_id: UUIDv7, *, lock: bool = False) -> _DeleteScope:
        """Resolve every physical Variant row owned by a Product, including archived rows."""
        statement = select(CatalogVariant.id).where(CatalogVariant.product_id == product_id)
        if lock:
            statement = statement.with_for_update()
        return _DeleteScope(
            product_id=product_id,
            variant_ids=tuple(self._session.scalars(statement).all()),
            include_product=True,
        )

    def _get_product(self, product_id: UUIDv7) -> CatalogProduct:
        """Return one live Product for preflight."""
        product = self._session.scalar(
            select(CatalogProduct).where(
                CatalogProduct.id == product_id,
                CatalogProduct.deleted_at.is_(None),
            )
        )
        if product is None:
            raise CatalogHardDeleteNotFoundError
        return product

    def _get_variant(self, variant_id: UUIDv7) -> CatalogVariant:
        """Return one live Variant for preflight."""
        variant = self._session.scalar(
            select(CatalogVariant).where(
                CatalogVariant.id == variant_id,
                CatalogVariant.deleted_at.is_(None),
            )
        )
        if variant is None:
            raise CatalogHardDeleteNotFoundError
        return variant

    def _lock_product(self, product_id: UUIDv7) -> CatalogProduct:
        """Lock the Product command target so delete eligibility cannot race."""
        product = self._session.scalar(
            select(CatalogProduct)
            .where(
                CatalogProduct.id == product_id,
                CatalogProduct.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if product is None:
            raise CatalogHardDeleteNotFoundError
        return product

    def _lock_variant(self, variant_id: UUIDv7) -> CatalogVariant:
        """Lock the Variant command target so delete eligibility cannot race."""
        variant = self._session.scalar(
            select(CatalogVariant)
            .where(
                CatalogVariant.id == variant_id,
                CatalogVariant.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if variant is None:
            raise CatalogHardDeleteNotFoundError
        return variant

    def _barcode_count(self, scope: _DeleteScope) -> int:
        return self._count(
            select(func.count(CatalogVariantBarcode.id)).where(
                CatalogVariantBarcode.variant_id.in_(scope.variant_ids)
            )
        )

    def _price_count(self, scope: _DeleteScope) -> int:
        return self._count(
            select(func.count(Price.id)).where(Price.variant_id.in_(scope.variant_ids))
        )

    def _image_link_count(self, scope: _DeleteScope) -> int:
        return self._count(
            select(func.count(ImageLink.id)).where(
                self._media_condition(scope, include_product=scope.include_product)
            )
        )

    def _stock_count(self, scope: _DeleteScope) -> int:
        return self._count(
            select(func.count(StockMovement.id)).where(
                StockMovement.variant_id.in_(scope.variant_ids)
            )
        )

    def _rental_count(self, scope: _DeleteScope) -> int:
        return self._count(
            select(func.count(RentalAssetRecord.id)).where(
                RentalAssetRecord.variant_id.in_(scope.variant_ids)
            )
        )

    def _publication_counts(self, scope: _DeleteScope) -> tuple[int, int]:
        publication_ids = tuple(
            self._session.scalars(
                select(Publication.id).where(Publication.variant_id.in_(scope.variant_ids))
            ).all()
        )
        attempts = 0
        if publication_ids:
            attempts = self._count(
                select(func.count(PublicationAttempt.id)).where(
                    PublicationAttempt.publication_id.in_(publication_ids)
                )
            )
        return len(publication_ids), attempts

    @staticmethod
    def _dependency(
        code: str,
        label: str,
        count: int,
    ) -> CatalogDeletionDependency | None:
        """Return a non-zero dependency projection."""
        if count == 0:
            return None
        return CatalogDeletionDependency(code=code, label=label, count=count)

    def _count(self, statement: object) -> int:
        """Execute a scalar count statement and normalize its result."""
        return int(self._session.scalar(statement) or 0)

    @staticmethod
    def _raise_if_blocked(preflight: CatalogDeletionPreflight) -> None:
        """Stop a command when the freshly calculated preflight has blockers."""
        if not preflight.can_delete:
            raise CatalogHardDeleteBlockedError(preflight)

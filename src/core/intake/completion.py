from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from core.activity.service import ActivityEventService, elapsed_seconds
from core.catalog.repository import (
    CatalogProductRepository,
    CatalogVariantRepository,
    CategoryRepository,
)
from core.catalog.schemas import CatalogProductCreate, CatalogVariantCreate
from core.catalog.service import CatalogProductService, CatalogVariantService
from core.intake.enums import IntakeItemKind, IntakeSessionStatus
from core.intake.models import IntakeItemDraft, IntakeSession
from core.intake.repository import IntakeSessionRepository
from core.intake.schemas import (
    IntakeCompletionItemRead,
    IntakeCompletionRead,
)
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.repository import ImageRepository
from core.media.schemas import ImageLinkCreate
from core.media.service import ImageLinkService
from core.readiness.service import ReadyForSaleService
from core.receipt.posting import ReceiptPostingService
from core.receipt.repository import ReceiptRepository
from core.receipt.schemas import ReceiptCreate, ReceiptItemCreate, ReceiptRead
from core.receipt.service import ReceiptItemService, ReceiptService
from core.rental.service import RentalAssetService
from core.shared.db import UUIDv7, generate_uuid_v7
from core.supplier.repository import SupplierRepository


class IntakeCompletionNotFoundError(Exception):
    """Raised when an IntakeSession is missing or belongs to another employee."""


class IntakeCompletionAbandonedError(Exception):
    """Raised when an abandoned session is submitted for completion."""


class IntakeCompletionIncompleteError(Exception):
    """Raised when a draft no longer contains every required valid fact."""


class CompleteIntakeWorkflow:
    """Convert one complete operational draft into catalog, Receipt, and ledger facts."""

    def __init__(self, session: Session) -> None:
        """Create one transaction coordinator from existing domain services."""
        self._session = session
        self._sessions = IntakeSessionRepository(session)
        self._suppliers = SupplierRepository(session)
        self._products = CatalogProductRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._categories = CategoryRepository(session)
        self._images = ImageRepository(session)
        self._receipts = ReceiptRepository(session)
        self._product_service = CatalogProductService(session)
        self._variant_service = CatalogVariantService(session)
        self._image_link_service = ImageLinkService(session)
        self._receipt_service = ReceiptService(session)
        self._receipt_item_service = ReceiptItemService(session)
        self._posting_service = ReceiptPostingService(session)
        self._readiness_service = ReadyForSaleService(session)
        self._rental_service = RentalAssetService(session)
        self._activity = ActivityEventService(session)

    def complete(self, session_id: UUIDv7, *, actor_id: UUIDv7) -> IntakeCompletionRead:
        """Complete once and return stable mappings with current readiness on retries."""
        try:
            intake_session = self._sessions.get_owned_for_update(session_id, actor_id)
            if intake_session is None:
                raise IntakeCompletionNotFoundError
            if intake_session.status is IntakeSessionStatus.COMPLETED:
                result = self._build_existing_result(intake_session)
                self._session.commit()
                return result
            if intake_session.status is IntakeSessionStatus.ABANDONED:
                raise IntakeCompletionAbandonedError

            active_items = [item for item in intake_session.items if item.abandoned_at is None]
            self._validate_completion(intake_session, active_items)

            receipt = self._receipt_service.open_receipt(
                ReceiptCreate(
                    supplier_id=intake_session.supplier_id,
                    receipt_date=date.today(),
                    notes=f"Created from IntakeSession {intake_session.id}",
                ),
                actor_id=actor_id,
            )

            completed_items: list[IntakeCompletionItemRead] = []
            for item in active_items:
                product_id, variant_id = self._materialize_item(item, actor_id=actor_id)
                self._receipt_item_service.add_item(
                    receipt.id,
                    ReceiptItemCreate(
                        variant_id=variant_id,
                        quantity=item.quantity,
                        purchase_price=item.purchase_price,
                    ),
                    actor_id=actor_id,
                )
                item.product_id = product_id
                item.variant_id = variant_id
                item.updated_by_id = actor_id
                self._rental_service.create_from_intake(
                    variant_id=variant_id,
                    intake_item_id=item.id,
                    quantity=item.rental_quantity,
                    actor_id=actor_id,
                )
                completed_items.append(
                    IntakeCompletionItemRead(
                        item_id=item.id,
                        product_id=product_id,
                        variant_id=variant_id,
                    )
                )

            self._posting_service.apply_posting(
                receipt.id,
                actor_id=actor_id,
            )
            intake_session.receipt_id = receipt.id
            intake_session.status = IntakeSessionStatus.COMPLETED
            completed_at = datetime.now(UTC)
            intake_session.completed_at = completed_at
            intake_session.updated_by_id = actor_id
            self._activity.record_intake_session_completed(
                session_id=intake_session.id,
                receipt_id=receipt.id,
                item_count=len(active_items),
                total_quantity=sum(item.quantity or 0 for item in active_items),
                duration_seconds=elapsed_seconds(intake_session.created_at, completed_at),
                actor_id=actor_id,
                occurred_at=completed_at,
            )
            self._session.flush()

            result = IntakeCompletionRead(
                session_id=intake_session.id,
                receipt=ReceiptRead.model_validate(receipt),
                items=completed_items,
                readiness=[
                    self._readiness_service.check_variant(item.variant_id)
                    for item in completed_items
                ],
            )
            self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise

    def _validate_completion(
        self,
        intake_session: IntakeSession,
        items: list[IntakeItemDraft],
    ) -> None:
        """Validate every mutable reference before creating durable business facts."""
        supplier = (
            self._suppliers.get(intake_session.supplier_id)
            if intake_session.supplier_id is not None
            else None
        )
        if supplier is None or not supplier.is_active or not items:
            raise IntakeCompletionIncompleteError
        for item in items:
            if item.quantity is None or item.purchase_price is None:
                raise IntakeCompletionIncompleteError
            if item.rental_quantity < 0 or item.rental_quantity > item.quantity:
                raise IntakeCompletionIncompleteError
            if item.kind is IntakeItemKind.EXISTING_VARIANT:
                variant = self._variants.get(item.variant_id) if item.variant_id else None
                if variant is None or not variant.is_active:
                    raise IntakeCompletionIncompleteError
            else:
                if item.image_id is None or self._images.get(item.image_id) is None:
                    raise IntakeCompletionIncompleteError
                if not (item.variant_title or "").strip():
                    raise IntakeCompletionIncompleteError
                if item.kind is IntakeItemKind.NEW_VARIANT:
                    product = self._products.get(item.product_id) if item.product_id else None
                    if product is None or not product.is_active:
                        raise IntakeCompletionIncompleteError
                else:
                    category = self._categories.get(item.category_id) if item.category_id else None
                    if (
                        category is None
                        or not category.is_active
                        or not (item.product_title or "").strip()
                    ):
                        raise IntakeCompletionIncompleteError

    def _materialize_item(
        self,
        item: IntakeItemDraft,
        *,
        actor_id: UUIDv7,
    ) -> tuple[UUIDv7, UUIDv7]:
        """Resolve a known Variant or create one catalog position and primary image."""
        if item.kind is IntakeItemKind.EXISTING_VARIANT:
            variant = self._variants.get(item.variant_id)
            if variant is None:
                raise IntakeCompletionIncompleteError
            return variant.product_id, variant.id

        if item.kind is IntakeItemKind.NEW_PRODUCT:
            product = self._product_service.create_product(
                CatalogProductCreate(
                    title=item.product_title or "",
                    slug=f"product-{generate_uuid_v7()}",
                    description=item.product_description,
                    category_id=item.category_id,
                ),
                actor_id=actor_id,
            )
            product_id = product.id
        else:
            if item.product_id is None:
                raise IntakeCompletionIncompleteError
            product_id = item.product_id

        variant = self._variant_service.create_variant(
            CatalogVariantCreate(
                product_id=product_id,
                title=item.variant_title or "",
                attributes=item.attributes,
            ),
            actor_id=actor_id,
        )
        self._image_link_service.create_link(
            ImageLinkCreate(
                image_id=item.image_id,
                entity_type=ImageLinkEntityType.CATALOG_VARIANT,
                entity_id=variant.id,
                role=ImageLinkRole.PRIMARY,
            ),
            actor_id=actor_id,
        )
        return product_id, variant.id

    def _build_existing_result(self, intake_session: IntakeSession) -> IntakeCompletionRead:
        """Reconstruct a completed response without creating duplicate business facts."""
        if intake_session.receipt_id is None:
            raise IntakeCompletionIncompleteError
        receipt = self._receipts.get(intake_session.receipt_id)
        if receipt is None:
            raise IntakeCompletionIncompleteError
        items: list[IntakeCompletionItemRead] = []
        for item in intake_session.items:
            if item.abandoned_at is not None:
                continue
            if item.product_id is None or item.variant_id is None:
                raise IntakeCompletionIncompleteError
            items.append(
                IntakeCompletionItemRead(
                    item_id=item.id,
                    product_id=item.product_id,
                    variant_id=item.variant_id,
                )
            )
        return IntakeCompletionRead(
            session_id=intake_session.id,
            receipt=ReceiptRead.model_validate(receipt),
            items=items,
            readiness=[self._readiness_service.check_variant(item.variant_id) for item in items],
        )

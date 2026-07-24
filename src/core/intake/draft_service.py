from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.activity.service import ActivityEventService, elapsed_seconds
from core.catalog.repository import (
    CatalogProductRepository,
    CatalogVariantRepository,
    CategoryRepository,
)
from core.intake.enums import (
    IntakeItemKind,
    IntakeSessionStatus,
)
from core.intake.models import IntakeItemDraft, IntakeSession
from core.intake.read_service import IntakeDraftReadService, IntakeSessionNotFoundError
from core.intake.repository import IntakeItemDraftRepository, IntakeSessionRepository
from core.intake.schemas import (
    ExistingIntakeItemCreate,
    IntakeItemDraftRead,
    IntakeItemDraftUpdate,
    IntakeSessionRead,
    IntakeSessionUpdate,
)
from core.media.models import Image
from core.media.service import ImageService
from core.shared.db import UUIDv7
from core.shared.money import quantize_money
from core.supplier.repository import SupplierRepository


class IntakeSessionNotDraftError(Exception):
    """Raised when an immutable session is mutated."""


class IntakeItemNotFoundError(Exception):
    """Raised when an item is missing from the owned session."""


class IntakeItemNotDraftError(Exception):
    """Raised when an abandoned item is mutated."""


class IntakeSupplierError(Exception):
    """Raised when a selected Supplier is unavailable."""


class IntakeVariantError(Exception):
    """Raised when a repeat-delivery Variant is unavailable."""


class IntakeProductError(Exception):
    """Raised when a Product for a new Variant is unavailable."""


class IntakeCategoryError(Exception):
    """Raised when a selected Category is unavailable."""


class IntakeItemFieldError(Exception):
    """Raised when fields do not belong to an item's identification path."""


class IntakeRentalQuantityError(Exception):
    """Raised when rental units exceed the total received quantity."""


class IntakeDraftWorkflow:
    """Business commands for resumable employee-owned intake drafts."""

    def __init__(self, session: Session, image_service: ImageService) -> None:
        """Create a service from existing persistence and media capabilities."""
        self._session = session
        self._image_service = image_service
        self._sessions = IntakeSessionRepository(session)
        self._items = IntakeItemDraftRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._products = CatalogProductRepository(session)
        self._categories = CategoryRepository(session)
        self._suppliers = SupplierRepository(session)
        self._reads = IntakeDraftReadService(session)
        self._activity = ActivityEventService(session)

    def create_session(self, *, actor_id: UUIDv7) -> IntakeSessionRead:
        """Start an empty resumable workspace without requiring a Supplier."""
        intake_session = IntakeSession(
            owner_id=actor_id,
            status=IntakeSessionStatus.DRAFT,
            created_by_id=actor_id,
        )
        self._sessions.add(intake_session)
        self._session.flush()
        self._activity.record_intake_session_started(
            session_id=intake_session.id,
            actor_id=actor_id,
        )
        self._session.commit()
        return self._reads.get_session(intake_session.id, actor_id=actor_id)

    def update_session(
        self,
        session_id: UUIDv7,
        data: IntakeSessionUpdate,
        *,
        actor_id: UUIDv7,
    ) -> IntakeSessionRead:
        """Set late administrative fields while the session remains a draft."""
        intake_session = self._get_owned_draft(session_id, actor_id)
        changes = data.model_dump(exclude_unset=True)
        if "supplier_id" in changes:
            self._ensure_supplier_is_active(data.supplier_id)
            intake_session.supplier_id = data.supplier_id
        intake_session.updated_by_id = actor_id
        self._session.commit()
        return self._reads.get_session(session_id, actor_id=actor_id)

    def add_existing_item(
        self,
        session_id: UUIDv7,
        data: ExistingIntakeItemCreate,
        *,
        actor_id: UUIDv7,
    ) -> IntakeItemDraftRead:
        """Add a known Variant by exact ID or scanner barcode without requiring a photo."""
        self._get_owned_draft(session_id, actor_id)
        variant = (
            self._variants.get(data.variant_id)
            if data.variant_id is not None
            else self._variants.get_active_by_barcode(data.barcode or "")
        )
        if variant is None or not variant.is_active:
            raise IntakeVariantError
        item = IntakeItemDraft(
            session_id=session_id,
            kind=IntakeItemKind.EXISTING_VARIANT,
            variant_id=variant.id,
            quantity=data.quantity,
            rental_quantity=data.rental_quantity,
            purchase_price=(
                quantize_money(data.purchase_price) if data.purchase_price is not None else None
            ),
            created_by_id=actor_id,
        )
        self._items.add(item)
        self._session.flush()
        self._activity.record_intake_item_added(
            session_id=session_id,
            item_id=item.id,
            kind=item.kind.value,
            actor_id=actor_id,
        )
        self._session.commit()
        self._session.refresh(item)
        return self._reads.build_item_read(item)

    def add_new_item(
        self,
        session_id: UUIDv7,
        original_filename: str,
        content: bytes,
        *,
        actor_id: UUIDv7,
        product_id: UUIDv7 | None = None,
    ) -> IntakeItemDraftRead:
        """Persist a new-item draft and its mandatory source photo together."""
        self._get_owned_draft(session_id, actor_id)
        if product_id is not None:
            self._ensure_product_is_active(product_id)

        image: Image | None = None
        committed = False
        try:
            image = self._image_service.upload_source_image(
                original_filename,
                content,
                actor_id=actor_id,
            )
            item = IntakeItemDraft(
                session_id=session_id,
                kind=(
                    IntakeItemKind.NEW_VARIANT
                    if product_id is not None
                    else IntakeItemKind.NEW_PRODUCT
                ),
                product_id=product_id,
                image_id=image.id,
                created_by_id=actor_id,
            )
            self._items.add(item)
            self._session.flush()
            self._activity.record_intake_item_added(
                session_id=session_id,
                item_id=item.id,
                kind=item.kind.value,
                actor_id=actor_id,
            )
            self._session.commit()
            committed = True
            self._session.refresh(item)
            return self._reads.build_item_read(item)
        except Exception:
            self._session.rollback()
            if image is not None and not committed:
                self._image_service.discard_uncommitted_source(image)
            raise

    def update_item(
        self,
        session_id: UUIDv7,
        item_id: UUIDv7,
        data: IntakeItemDraftUpdate,
        *,
        actor_id: UUIDv7,
    ) -> IntakeItemDraftRead:
        """Persist progressive item data without creating catalog or stock records."""
        self._get_owned_draft(session_id, actor_id)
        item = self._get_draft_item(session_id, item_id)
        changes = data.model_dump(exclude_unset=True)
        self._ensure_fields_match_kind(item.kind, changes)
        self._ensure_rental_quantity_is_valid(item, changes)
        if "category_id" in changes and data.category_id is not None:
            self._ensure_category_is_active(data.category_id)
        if "purchase_price" in changes and data.purchase_price is not None:
            changes["purchase_price"] = quantize_money(data.purchase_price)
        for field, value in changes.items():
            setattr(item, field, value)
        item.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(item)
        return self._reads.build_item_read(item)

    def abandon_item(
        self,
        session_id: UUIDv7,
        item_id: UUIDv7,
        reason: str,
        *,
        actor_id: UUIDv7,
    ) -> IntakeItemDraftRead:
        """Explicitly abandon one unfinished position without deleting evidence."""
        self._get_owned_draft(session_id, actor_id)
        item = self._get_draft_item(session_id, item_id)
        item.abandoned_at = datetime.now(UTC)
        item.abandonment_reason = reason
        item.updated_by_id = actor_id
        self._activity.record_intake_item_abandoned(
            session_id=session_id,
            item_id=item.id,
            reason=reason,
            actor_id=actor_id,
        )
        self._session.commit()
        self._session.refresh(item)
        return self._reads.build_item_read(item)

    def abandon_session(
        self,
        session_id: UUIDv7,
        reason: str,
        *,
        actor_id: UUIDv7,
    ) -> IntakeSessionRead:
        """Explicitly close unfinished work while preserving it for history."""
        intake_session = self._get_owned_draft(session_id, actor_id)
        abandoned_at = datetime.now(UTC)
        intake_session.status = IntakeSessionStatus.ABANDONED
        intake_session.abandoned_at = abandoned_at
        intake_session.abandonment_reason = reason
        intake_session.updated_by_id = actor_id
        self._activity.record_intake_session_abandoned(
            session_id=session_id,
            reason=reason,
            duration_seconds=elapsed_seconds(intake_session.created_at, abandoned_at),
            actor_id=actor_id,
            occurred_at=abandoned_at,
        )
        self._session.commit()
        return self._reads.get_session(session_id, actor_id=actor_id)

    def _get_owned_session(self, session_id: UUIDv7, actor_id: UUIDv7) -> IntakeSession:
        """Hide sessions belonging to other employees behind normal not-found semantics."""
        intake_session = self._sessions.get_owned(session_id, actor_id)
        if intake_session is None:
            raise IntakeSessionNotFoundError
        return intake_session

    def _get_owned_draft(self, session_id: UUIDv7, actor_id: UUIDv7) -> IntakeSession:
        """Return an owned mutable session."""
        intake_session = self._get_owned_session(session_id, actor_id)
        if intake_session.status is not IntakeSessionStatus.DRAFT:
            raise IntakeSessionNotDraftError
        return intake_session

    def _get_draft_item(self, session_id: UUIDv7, item_id: UUIDv7) -> IntakeItemDraft:
        """Return one non-abandoned item from an owned draft session."""
        item = self._items.get(session_id, item_id)
        if item is None:
            raise IntakeItemNotFoundError
        if item.abandoned_at is not None:
            raise IntakeItemNotDraftError
        return item

    def _ensure_supplier_is_active(self, supplier_id: UUIDv7 | None) -> None:
        """Allow removing a late Supplier but reject unavailable selections."""
        if supplier_id is None:
            return
        supplier = self._suppliers.get(supplier_id)
        if supplier is None or not supplier.is_active:
            raise IntakeSupplierError

    def _ensure_product_is_active(self, product_id: UUIDv7) -> None:
        """Require an active Product when adding a new Variant photo."""
        product = self._products.get(product_id)
        if product is None or not product.is_active:
            raise IntakeProductError

    def _ensure_category_is_active(self, category_id: UUIDv7) -> None:
        """Reject inactive or unavailable Categories as soon as they are selected."""
        category = self._categories.get(category_id)
        if category is None or not category.is_active:
            raise IntakeCategoryError

    def _ensure_fields_match_kind(
        self,
        kind: IntakeItemKind,
        changes: dict[str, object],
    ) -> None:
        """Prevent one identification path from accumulating unrelated fields."""
        common = {"quantity", "rental_quantity", "purchase_price"}
        if kind is IntakeItemKind.EXISTING_VARIANT:
            allowed = common
        elif kind is IntakeItemKind.NEW_VARIANT:
            allowed = common | {"variant_title", "attributes"}
        else:
            allowed = common | {
                "category_id",
                "product_title",
                "product_description",
                "variant_title",
                "attributes",
            }
        if changes.keys() - allowed:
            raise IntakeItemFieldError

    def _ensure_rental_quantity_is_valid(
        self,
        item: IntakeItemDraft,
        changes: dict[str, object],
    ) -> None:
        """Keep rental allocation within the final received quantity."""
        quantity = changes.get("quantity", item.quantity)
        rental_quantity = changes.get("rental_quantity", item.rental_quantity)
        if (
            isinstance(quantity, int)
            and isinstance(rental_quantity, int)
            and rental_quantity > quantity
        ):
            raise IntakeRentalQuantityError

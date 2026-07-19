from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import (
    CatalogProductRepository,
    CatalogVariantRepository,
    CategoryRepository,
)
from core.intake.enums import (
    IntakeItemKind,
    IntakeItemRequirement,
    IntakeSessionRequirement,
    IntakeSessionStatus,
)
from core.intake.models import IntakeItemDraft, IntakeSession
from core.intake.repository import IntakeItemDraftRepository, IntakeSessionRepository
from core.intake.schemas import (
    ExistingIntakeItemCreate,
    IntakeItemDraftRead,
    IntakeItemDraftUpdate,
    IntakeSessionRead,
    IntakeSessionUpdate,
)
from core.media.models import Image
from core.media.repository import ImageRepository
from core.media.service import ImageService
from core.shared.db import UUIDv7
from core.shared.money import quantize_money
from core.supplier.repository import SupplierRepository


class IntakeSessionNotFoundError(Exception):
    """Raised when a session is missing or does not belong to the actor."""


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


class IntakeDraftService:
    """Business operations for resumable employee-owned intake drafts."""

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
        self._images = ImageRepository(session)

    def create_session(self, *, actor_id: UUIDv7) -> IntakeSessionRead:
        """Start an empty resumable workspace without requiring a Supplier."""
        intake_session = IntakeSession(
            owner_id=actor_id,
            status=IntakeSessionStatus.DRAFT,
            created_by_id=actor_id,
        )
        self._sessions.add(intake_session)
        self._session.commit()
        return self.get_session(intake_session.id, actor_id=actor_id)

    def list_sessions(
        self,
        *,
        actor_id: UUIDv7,
        status: IntakeSessionStatus | None = None,
    ) -> Sequence[IntakeSessionRead]:
        """Return resumable work owned by the current employee."""
        return [
            self._build_session_read(intake_session)
            for intake_session in self._sessions.list_owned(actor_id, status=status)
        ]

    def get_session(self, session_id: UUIDv7, *, actor_id: UUIDv7) -> IntakeSessionRead:
        """Return one owned session with derived missing requirements."""
        return self._build_session_read(self._get_owned_session(session_id, actor_id))

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
        return self.get_session(session_id, actor_id=actor_id)

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
            purchase_price=(
                quantize_money(data.purchase_price) if data.purchase_price is not None else None
            ),
            created_by_id=actor_id,
        )
        self._items.add(item)
        self._session.commit()
        self._session.refresh(item)
        return self._build_item_read(item)

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
                commit=False,
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
            self._session.commit()
            committed = True
            self._session.refresh(item)
            return self._build_item_read(item)
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
        if "category_id" in changes and data.category_id is not None:
            self._ensure_category_is_active(data.category_id)
        if "purchase_price" in changes and data.purchase_price is not None:
            changes["purchase_price"] = quantize_money(data.purchase_price)
        for field, value in changes.items():
            setattr(item, field, value)
        item.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(item)
        return self._build_item_read(item)

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
        self._session.commit()
        self._session.refresh(item)
        return self._build_item_read(item)

    def abandon_session(
        self,
        session_id: UUIDv7,
        reason: str,
        *,
        actor_id: UUIDv7,
    ) -> IntakeSessionRead:
        """Explicitly close unfinished work while preserving it for history."""
        intake_session = self._get_owned_draft(session_id, actor_id)
        intake_session.status = IntakeSessionStatus.ABANDONED
        intake_session.abandoned_at = datetime.now(UTC)
        intake_session.abandonment_reason = reason
        intake_session.updated_by_id = actor_id
        self._session.commit()
        return self.get_session(session_id, actor_id=actor_id)

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
        common = {"quantity", "purchase_price"}
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

    def _build_item_read(self, item: IntakeItemDraft) -> IntakeItemDraftRead:
        """Attach deterministic completeness information to a persisted item."""
        return IntakeItemDraftRead.model_validate(item).model_copy(
            update={"missing_requirements": self._item_requirements(item)}
        )

    def _build_session_read(self, intake_session: IntakeSession) -> IntakeSessionRead:
        """Attach item and session completeness without persisting duplicate statuses."""
        items = [self._build_item_read(item) for item in intake_session.items]
        active_items = [item for item in items if item.abandoned_at is None]
        missing: list[IntakeSessionRequirement] = []
        if intake_session.supplier_id is None:
            missing.append(IntakeSessionRequirement.MISSING_SUPPLIER)
        if not active_items:
            missing.append(IntakeSessionRequirement.MISSING_ITEMS)
        elif any(item.missing_requirements for item in active_items):
            missing.append(IntakeSessionRequirement.INCOMPLETE_ITEMS)
        return IntakeSessionRead.model_validate(intake_session).model_copy(
            update={"items": items, "missing_requirements": missing}
        )

    def _item_requirements(self, item: IntakeItemDraft) -> list[IntakeItemRequirement]:
        """Return stable, kind-specific missing requirements for one draft."""
        missing: list[IntakeItemRequirement] = []
        if item.kind is IntakeItemKind.EXISTING_VARIANT:
            variant = self._variants.get(item.variant_id) if item.variant_id is not None else None
            if variant is None or not variant.is_active:
                missing.append(IntakeItemRequirement.MISSING_VARIANT)
        else:
            if item.image_id is None or self._images.get(item.image_id) is None:
                missing.append(IntakeItemRequirement.MISSING_IMAGE)

        if item.kind is IntakeItemKind.NEW_VARIANT:
            product = self._products.get(item.product_id) if item.product_id is not None else None
            if product is None or not product.is_active:
                missing.append(IntakeItemRequirement.MISSING_PRODUCT)
        elif item.kind is IntakeItemKind.NEW_PRODUCT:
            category = (
                self._categories.get(item.category_id) if item.category_id is not None else None
            )
            if category is None or not category.is_active:
                missing.append(IntakeItemRequirement.MISSING_CATEGORY)
            if not (item.product_title or "").strip():
                missing.append(IntakeItemRequirement.MISSING_PRODUCT_TITLE)

        if (
            item.kind in {IntakeItemKind.NEW_PRODUCT, IntakeItemKind.NEW_VARIANT}
            and not (item.variant_title or "").strip()
        ):
            missing.append(IntakeItemRequirement.MISSING_VARIANT_TITLE)
        if item.quantity is None:
            missing.append(IntakeItemRequirement.MISSING_QUANTITY)
        if item.purchase_price is None:
            missing.append(IntakeItemRequirement.MISSING_PURCHASE_PRICE)
        return missing

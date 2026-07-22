from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.intake.completeness import (
    IntakeItemAvailability,
    derive_item_requirements,
    derive_session_requirements,
)
from core.intake.enums import IntakeSessionStatus
from core.intake.models import IntakeItemDraft, IntakeSession
from core.intake.repository import IntakeSessionRepository
from core.intake.schemas import IntakeItemDraftRead, IntakeSessionRead
from core.media.models import Image
from core.shared.db import UUIDv7


class IntakeSessionNotFoundError(Exception):
    """Raised when a session is missing or does not belong to the actor."""


class IntakeDraftReadService:
    """Build employee-owned IntakeSession projections with derived completeness."""

    def __init__(self, session: Session) -> None:
        """Create a read service bound to one request-scoped database session."""
        self._session = session
        self._sessions = IntakeSessionRepository(session)

    def list_sessions(
        self,
        *,
        actor_id: UUIDv7,
        status: IntakeSessionStatus | None = None,
    ) -> Sequence[IntakeSessionRead]:
        """Return resumable work owned by the current employee."""
        sessions = self._sessions.list_owned(actor_id, status=status)
        availability = self._load_availability(
            [item for intake_session in sessions for item in intake_session.items]
        )
        return [self._build_session_read(item, availability) for item in sessions]

    def get_session(self, session_id: UUIDv7, *, actor_id: UUIDv7) -> IntakeSessionRead:
        """Return one owned session with derived missing requirements."""
        intake_session = self._sessions.get_owned(session_id, actor_id)
        if intake_session is None:
            raise IntakeSessionNotFoundError
        availability = self._load_availability(intake_session.items)
        return self._build_session_read(intake_session, availability)

    def build_item_read(self, item: IntakeItemDraft) -> IntakeItemDraftRead:
        """Build one command result through the same completeness projection."""
        availability = self._load_availability([item])
        return self._build_item_read(item, availability)

    def _build_session_read(
        self,
        intake_session: IntakeSession,
        availability: dict[UUIDv7, IntakeItemAvailability],
    ) -> IntakeSessionRead:
        """Attach derived item and session requirements without persisted workflow state."""
        items = [self._build_item_read(item, availability) for item in intake_session.items]
        active_requirements = [
            item.missing_requirements for item in items if item.abandoned_at is None
        ]
        return IntakeSessionRead.model_validate(intake_session).model_copy(
            update={
                "items": items,
                "missing_requirements": derive_session_requirements(
                    has_supplier=intake_session.supplier_id is not None,
                    active_item_requirements=active_requirements,
                ),
            }
        )

    def _build_item_read(
        self,
        item: IntakeItemDraft,
        availability: dict[UUIDv7, IntakeItemAvailability],
    ) -> IntakeItemDraftRead:
        """Attach deterministic completeness information to one persisted item."""
        return IntakeItemDraftRead.model_validate(item).model_copy(
            update={
                "missing_requirements": derive_item_requirements(
                    item,
                    availability[item.id],
                )
            }
        )

    def _load_availability(
        self,
        items: Sequence[IntakeItemDraft],
    ) -> dict[UUIDv7, IntakeItemAvailability]:
        """Resolve referenced facts in bounded bulk queries instead of per-item reads."""
        variant_ids = {item.variant_id for item in items if item.variant_id is not None}
        image_ids = {item.image_id for item in items if item.image_id is not None}
        product_ids = {item.product_id for item in items if item.product_id is not None}
        category_ids = {item.category_id for item in items if item.category_id is not None}

        active_variants = self._active_ids(CatalogVariant, variant_ids)
        active_products = self._active_ids(CatalogProduct, product_ids)
        active_categories = self._active_ids(Category, category_ids)
        available_images = self._present_ids(Image, image_ids)

        return {
            item.id: IntakeItemAvailability(
                variant=item.variant_id in active_variants,
                image=item.image_id in available_images,
                product=item.product_id in active_products,
                category=item.category_id in active_categories,
            )
            for item in items
        }

    def _active_ids(self, model: type, ids: set[UUIDv7]) -> set[UUIDv7]:
        """Return non-deleted active identifiers for one catalog model."""
        if not ids:
            return set()
        statement = select(model.id).where(
            model.id.in_(ids),
            model.deleted_at.is_(None),
            model.is_active.is_(True),
        )
        return set(self._session.scalars(statement).all())

    def _present_ids(self, model: type, ids: set[UUIDv7]) -> set[UUIDv7]:
        """Return non-deleted identifiers for a referenced fact model."""
        if not ids:
            return set()
        statement = select(model.id).where(model.id.in_(ids), model.deleted_at.is_(None))
        return set(self._session.scalars(statement).all())

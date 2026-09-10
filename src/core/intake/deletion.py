from __future__ import annotations

from sqlalchemy.orm import Session

from core.intake.enums import IntakeSessionStatus
from core.intake.repository import IntakeSessionRepository
from core.media.service import ImageService
from core.shared.db import UUIDv7


class IntakeDraftDeleteForbiddenError(Exception):
    """Raised when a non-administrator requests destructive draft removal."""


class IntakeDraftDeleteNotFoundError(Exception):
    """Raised when the target is absent or was already deleted."""


class IntakeDraftDeleteStatusError(Exception):
    """Raised when immutable completed business history is targeted."""


class DeleteIntakeDraftWorkflow:
    """Atomically remove an unposted Intake workspace without creating business facts."""

    def __init__(self, session: Session) -> None:
        """Bind repositories to the caller-owned transaction."""
        self._session = session
        self._sessions = IntakeSessionRepository(session)
        self._images = ImageService(session)

    def delete(self, session_id: UUIDv7, *, actor_id: UUIDv7, is_admin: bool) -> None:
        """Delete one Draft and its items; preserve consumed identity sequence values."""
        if not is_admin:
            raise IntakeDraftDeleteForbiddenError
        intake = self._sessions.get_for_update(session_id)
        if intake is None:
            raise IntakeDraftDeleteNotFoundError
        if intake.status is not IntakeSessionStatus.DRAFT or intake.receipt_id is not None:
            raise IntakeDraftDeleteStatusError

        items = list(intake.items)
        image_ids = {item.image_id for item in items if item.image_id is not None}
        # Self-FK is RESTRICT: dependent Variants must go before their Product draft.
        items.sort(key=lambda item: item.draft_product_item_id is None)
        for item in items:
            self._session.delete(item)
            # Flush separately so SQLAlchemy cannot batch a Product draft delete
            # together with the Variant drafts that still reference it.
            self._session.flush()
        for image_id in image_ids:
            self._images.delete_image(image_id, actor_id=actor_id)
        self._session.delete(intake)
        self._session.commit()

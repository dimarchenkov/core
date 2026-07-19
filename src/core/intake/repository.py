from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from core.intake.enums import IntakeSessionStatus
from core.intake.models import IntakeItemDraft, IntakeSession
from core.shared.db import UUIDv7


class IntakeSessionRepository:
    """Database access for employee-owned intake workspaces."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to one database session."""
        self._session = session

    def add(self, intake_session: IntakeSession) -> IntakeSession:
        """Add an IntakeSession to the current unit of work."""
        self._session.add(intake_session)
        return intake_session

    def get_owned(self, session_id: UUIDv7, owner_id: UUIDv7) -> IntakeSession | None:
        """Return one active session only when it belongs to the caller."""
        statement = (
            select(IntakeSession)
            .options(selectinload(IntakeSession.items))
            .where(
                IntakeSession.id == session_id,
                IntakeSession.owner_id == owner_id,
                IntakeSession.deleted_at.is_(None),
            )
        )
        return self._session.scalar(statement)

    def get_owned_for_update(
        self,
        session_id: UUIDv7,
        owner_id: UUIDv7,
    ) -> IntakeSession | None:
        """Lock one owned session for an idempotent lifecycle transition."""
        statement = (
            select(IntakeSession)
            .options(selectinload(IntakeSession.items))
            .where(
                IntakeSession.id == session_id,
                IntakeSession.owner_id == owner_id,
                IntakeSession.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def list_owned(
        self,
        owner_id: UUIDv7,
        *,
        status: IntakeSessionStatus | None = None,
    ) -> Sequence[IntakeSession]:
        """Return caller-owned sessions with most recently touched work first."""
        statement = (
            select(IntakeSession)
            .options(selectinload(IntakeSession.items))
            .where(
                IntakeSession.owner_id == owner_id,
                IntakeSession.deleted_at.is_(None),
            )
            .order_by(IntakeSession.updated_at.desc(), IntakeSession.created_at.desc())
        )
        if status is not None:
            statement = statement.where(IntakeSession.status == status)
        return self._session.scalars(statement).all()


class IntakeItemDraftRepository:
    """Database access for positions inside intake workspaces."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to one database session."""
        self._session = session

    def add(self, item: IntakeItemDraft) -> IntakeItemDraft:
        """Add an item draft to the current unit of work."""
        self._session.add(item)
        return item

    def get(self, session_id: UUIDv7, item_id: UUIDv7) -> IntakeItemDraft | None:
        """Return one active item belonging to the requested IntakeSession."""
        statement = select(IntakeItemDraft).where(
            IntakeItemDraft.id == item_id,
            IntakeItemDraft.session_id == session_id,
            IntakeItemDraft.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

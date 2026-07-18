from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.integrations.aqsi.enums import PublicationChannel
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.shared.db import UUIDv7


class PublicationRepository:
    """Database access for current external publication projections."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current session."""
        self._session = session

    def add(self, publication: Publication) -> Publication:
        """Add a publication to the current unit of work."""
        self._session.add(publication)
        return publication

    def get(self, publication_id: UUIDv7, *, for_update: bool = False) -> Publication | None:
        """Return an active publication, optionally locking it."""
        statement = select(Publication).where(
            Publication.id == publication_id,
            Publication.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_for_variant(
        self,
        variant_id: UUIDv7,
        channel: PublicationChannel,
        *,
        for_update: bool = False,
    ) -> Publication | None:
        """Return one active channel projection for a Variant."""
        statement = select(Publication).where(
            Publication.variant_id == variant_id,
            Publication.channel == channel,
            Publication.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)


class PublicationAttemptRepository:
    """Database access for attributed publication attempt history."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current session."""
        self._session = session

    def add(self, attempt: PublicationAttempt) -> PublicationAttempt:
        """Add an attempt to the current unit of work."""
        self._session.add(attempt)
        return attempt

    def get(self, attempt_id: UUIDv7, *, for_update: bool = False) -> PublicationAttempt | None:
        """Return an attempt, optionally locking it."""
        statement = select(PublicationAttempt).where(PublicationAttempt.id == attempt_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def next_number(self, publication_id: UUIDv7) -> int:
        """Return the next monotonic attempt number for a publication."""
        statement = select(func.coalesce(func.max(PublicationAttempt.attempt_number), 0)).where(
            PublicationAttempt.publication_id == publication_id
        )
        return int(self._session.scalar(statement) or 0) + 1

    def latest_pending(self, publication_id: UUIDv7) -> PublicationAttempt | None:
        """Return the latest unfinished attempt for idempotent commands."""
        from core.integrations.aqsi.enums import PublicationAttemptStatus

        statement = (
            select(PublicationAttempt)
            .where(
                PublicationAttempt.publication_id == publication_id,
                PublicationAttempt.status.in_(
                    [
                        PublicationAttemptStatus.PENDING,
                        PublicationAttemptStatus.PROCESSING,
                        PublicationAttemptStatus.ACCEPTED,
                    ]
                ),
            )
            .order_by(PublicationAttempt.attempt_number.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def latest(self, publication_id: UUIDv7) -> PublicationAttempt | None:
        """Return the latest attempt regardless of lifecycle state."""
        statement = (
            select(PublicationAttempt)
            .where(PublicationAttempt.publication_id == publication_id)
            .order_by(PublicationAttempt.attempt_number.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def list_for_publication(self, publication_id: UUIDv7) -> Sequence[PublicationAttempt]:
        """Return all attempts newest first."""
        statement = (
            select(PublicationAttempt)
            .where(PublicationAttempt.publication_id == publication_id)
            .order_by(PublicationAttempt.attempt_number.desc())
        )
        return self._session.scalars(statement).all()

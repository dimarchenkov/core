from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.activity.enums import ActivityEventType
from core.activity.models import ActivityEvent
from core.shared.db import UUIDv7


class ActivityEventRepository:
    """Persistence operations for the append-only operational event stream."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to one request-scoped Session."""
        self._session = session

    def add(self, event: ActivityEvent) -> ActivityEvent:
        """Append an event to the caller-owned transaction."""
        self._session.add(event)
        return event

    def list_for_actor(
        self,
        actor_id: UUIDv7,
        *,
        event_type: ActivityEventType | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[ActivityEvent]:
        """Return one employee's newest operational outcomes first."""
        statement = select(ActivityEvent).where(ActivityEvent.actor_id == actor_id)
        if event_type is not None:
            statement = statement.where(ActivityEvent.event_type == event_type)
        statement = (
            statement.order_by(
                ActivityEvent.occurred_at.desc(),
                ActivityEvent.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return self._session.scalars(statement).all()

    def count_for_actor(
        self,
        actor_id: UUIDv7,
        *,
        event_type: ActivityEventType | None = None,
    ) -> int:
        """Count one employee's events under the same optional filter."""
        statement = (
            select(func.count())
            .select_from(ActivityEvent)
            .where(ActivityEvent.actor_id == actor_id)
        )
        if event_type is not None:
            statement = statement.where(ActivityEvent.event_type == event_type)
        return int(self._session.scalar(statement) or 0)

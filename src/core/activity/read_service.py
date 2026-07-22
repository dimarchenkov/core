from __future__ import annotations

from sqlalchemy.orm import Session

from core.activity.enums import ActivityEventType
from core.activity.repository import ActivityEventRepository
from core.activity.schemas import ActivityEventPage, ActivityEventRead
from core.shared.db import UUIDv7


class ActivityReadService:
    """Build the current employee's operational activity feed."""

    def __init__(self, session: Session) -> None:
        """Create a read service bound to one request-scoped Session."""
        self._events = ActivityEventRepository(session)

    def list_for_actor(
        self,
        actor_id: UUIDv7,
        *,
        event_type: ActivityEventType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ActivityEventPage:
        """Return only the caller's events with stable pagination metadata."""
        events = self._events.list_for_actor(
            actor_id,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
        return ActivityEventPage(
            items=[ActivityEventRead.model_validate(event) for event in events],
            total=self._events.count_for_actor(actor_id, event_type=event_type),
            limit=limit,
            offset=offset,
        )

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from core.activity.enums import ActivityEntityType, ActivityEventType
from core.activity.models import ActivityEvent
from core.activity.repository import ActivityEventRepository
from core.shared.db import UUIDv7


class ActivityEventService:
    """Append the small supported set of operational facts without committing."""

    def __init__(self, session: Session) -> None:
        """Create a transaction-neutral event service."""
        self._events = ActivityEventRepository(session)

    def record_intake_session_started(
        self,
        *,
        session_id: UUIDv7,
        actor_id: UUIDv7,
    ) -> ActivityEvent:
        """Record creation of a resumable employee workspace."""
        return self._append(
            ActivityEventType.INTAKE_SESSION_STARTED,
            actor_id=actor_id,
            entity_type=ActivityEntityType.INTAKE_SESSION,
            entity_id=session_id,
        )

    def record_intake_item_added(
        self,
        *,
        session_id: UUIDv7,
        item_id: UUIDv7,
        kind: str,
        actor_id: UUIDv7,
    ) -> ActivityEvent:
        """Record successful physical-item identification inside a session."""
        return self._append(
            ActivityEventType.INTAKE_ITEM_ADDED,
            actor_id=actor_id,
            entity_type=ActivityEntityType.INTAKE_ITEM,
            entity_id=item_id,
            data={"session_id": str(session_id), "kind": kind},
        )

    def record_intake_item_abandoned(
        self,
        *,
        session_id: UUIDv7,
        item_id: UUIDv7,
        reason: str,
        actor_id: UUIDv7,
    ) -> ActivityEvent:
        """Record explicit abandonment of one unfinished position."""
        return self._append(
            ActivityEventType.INTAKE_ITEM_ABANDONED,
            actor_id=actor_id,
            entity_type=ActivityEntityType.INTAKE_ITEM,
            entity_id=item_id,
            data={"session_id": str(session_id), "reason": reason},
        )

    def record_intake_session_completed(
        self,
        *,
        session_id: UUIDv7,
        receipt_id: UUIDv7,
        item_count: int,
        total_quantity: int,
        duration_seconds: int,
        actor_id: UUIDv7,
        occurred_at: datetime,
    ) -> ActivityEvent:
        """Record the successful atomic business outcome of Intake completion."""
        return self._append(
            ActivityEventType.INTAKE_SESSION_COMPLETED,
            actor_id=actor_id,
            entity_type=ActivityEntityType.INTAKE_SESSION,
            entity_id=session_id,
            occurred_at=occurred_at,
            data={
                "receipt_id": str(receipt_id),
                "item_count": item_count,
                "total_quantity": total_quantity,
                "duration_seconds": duration_seconds,
            },
        )

    def record_intake_session_abandoned(
        self,
        *,
        session_id: UUIDv7,
        reason: str,
        duration_seconds: int,
        actor_id: UUIDv7,
        occurred_at: datetime,
    ) -> ActivityEvent:
        """Record explicit closure of unfinished work."""
        return self._append(
            ActivityEventType.INTAKE_SESSION_ABANDONED,
            actor_id=actor_id,
            entity_type=ActivityEntityType.INTAKE_SESSION,
            entity_id=session_id,
            occurred_at=occurred_at,
            data={"reason": reason, "duration_seconds": duration_seconds},
        )

    def _append(
        self,
        event_type: ActivityEventType,
        *,
        actor_id: UUIDv7,
        entity_type: ActivityEntityType,
        entity_id: UUIDv7,
        data: dict[str, str | int] | None = None,
        occurred_at: datetime | None = None,
    ) -> ActivityEvent:
        """Append one supported fact to the current transaction without finalizing it."""
        event = ActivityEvent(
            event_type=event_type,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data or {},
        )
        if occurred_at is not None:
            event.occurred_at = occurred_at
        return self._events.add(event)


def elapsed_seconds(started_at: datetime, ended_at: datetime) -> int:
    """Return a non-negative whole-second duration across SQLite/PostgreSQL timezone behavior."""
    if started_at.tzinfo is None and ended_at.tzinfo is not None:
        ended_at = ended_at.replace(tzinfo=None)
    elif started_at.tzinfo is not None and ended_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=None)
    return max(0, int((ended_at - started_at).total_seconds()))

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.activity.enums import ActivityEntityType, ActivityEventType
from core.shared.db import Base, UUIDPrimaryKeyMixin, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable enum values for database persistence."""
    return [member.value for member in enum_class]


class ActivityEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only operational fact attributed to one employee."""

    __tablename__ = "activity_events"

    event_type: Mapped[ActivityEventType] = mapped_column(
        Enum(ActivityEventType, name="activity_event_type", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[ActivityEntityType] = mapped_column(
        Enum(ActivityEntityType, name="activity_entity_type", values_callable=_enum_values),
        nullable=False,
    )
    entity_id: Mapped[UUIDv7] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        index=True,
    )
    data: Mapped[dict[str, str | int]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

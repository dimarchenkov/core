from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.activity.enums import ActivityEntityType, ActivityEventType
from core.shared.db import UUIDv7


class ActivityEventRead(PydanticBaseModel):
    """One append-only operational fact safe for the employee feed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    event_type: ActivityEventType
    actor_id: UUIDv7
    entity_type: ActivityEntityType
    entity_id: UUIDv7
    occurred_at: datetime
    data: dict[str, str | int]


class ActivityEventPage(PydanticBaseModel):
    """Paginated reverse-chronological activity feed."""

    items: list[ActivityEventRead]
    total: int
    limit: int
    offset: int

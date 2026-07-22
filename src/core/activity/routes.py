from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.activity.enums import ActivityEventType
from core.activity.read_service import ActivityReadService
from core.activity.schemas import ActivityEventPage
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User

router = APIRouter(
    prefix="/api/activity",
    tags=["activity"],
    dependencies=[Depends(get_current_user)],
)


def get_activity_read_service(
    session: Annotated[Session, Depends(get_session)],
) -> ActivityReadService:
    """Provide operational feed projections."""
    return ActivityReadService(session)


@router.get("/me", response_model=ActivityEventPage)
def list_my_activity(
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ActivityReadService, Depends(get_activity_read_service)],
    event_type: ActivityEventType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActivityEventPage:
    """Return meaningful outcomes attributed to the authenticated employee."""
    return service.list_for_actor(
        current_user.id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )

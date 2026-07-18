from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from redis.exceptions import RedisError
from rq import Queue, Retry
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.integrations.aqsi.jobs import publish_aqsi_attempt
from core.integrations.aqsi.payload import (
    AqsiVariantNotFoundError,
    AqsiVariantNotReadyError,
)
from core.integrations.aqsi.schemas import (
    PublicationAttemptRead,
    PublicationRead,
    PublicationRequestRead,
)
from core.integrations.aqsi.service import (
    AqsiIntegrationDisabledError,
    AqsiIntegrationNotConfiguredError,
    AqsiPublicationService,
    PublicationNotFoundError,
)
from core.jobs import get_default_queue
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/publishing/aqsi/variants",
    tags=["publishing", "aqsi"],
    dependencies=[Depends(get_current_user)],
)


def get_aqsi_publication_service(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AqsiPublicationService:
    """Provide AQSI publication services for request handlers."""
    return AqsiPublicationService(session, settings)


def get_aqsi_queue() -> Queue:
    """Provide the infrastructure queue used for AQSI publication jobs."""
    return get_default_queue()


@router.post(
    "/{variant_id}",
    response_model=PublicationRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_variant(
    variant_id: UUIDv7,
    service: Annotated[AqsiPublicationService, Depends(get_aqsi_publication_service)],
    queue: Annotated[Queue, Depends(get_aqsi_queue)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PublicationRequestRead:
    """Validate and enqueue an idempotent AQSI product publication."""
    try:
        publication, attempt, should_enqueue = service.request_publication(
            variant_id,
            actor_id=current_user.id,
        )
    except AqsiVariantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Catalog variant not found.") from exc
    except AqsiVariantNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"missing_requirements": exc.missing_requirements},
        ) from exc
    except AqsiIntegrationDisabledError as exc:
        raise HTTPException(status_code=503, detail="AQSI integration is disabled.") from exc
    except AqsiIntegrationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail="AQSI API key is not configured.") from exc

    if should_enqueue:
        try:
            queue.enqueue(
                publish_aqsi_attempt,
                str(attempt.id),
                job_timeout=120,
                retry=Retry(max=3, interval=[2, 10, 30]),
            )
        except RedisError as exc:
            service.mark_enqueue_failed(attempt.id)
            raise HTTPException(
                status_code=503,
                detail="AQSI publication could not be queued.",
            ) from exc

    return PublicationRequestRead(
        publication=PublicationRead.model_validate(publication).model_copy(
            update={"is_outdated": service.is_outdated(publication)}
        ),
        attempt=PublicationAttemptRead.model_validate(attempt),
        queued=should_enqueue,
    )


@router.get("/{variant_id}", response_model=PublicationRead)
def get_publication(
    variant_id: UUIDv7,
    service: Annotated[AqsiPublicationService, Depends(get_aqsi_publication_service)],
) -> PublicationRead:
    """Return current AQSI projection state for a Variant."""
    try:
        publication = service.get_publication(variant_id)
        return PublicationRead.model_validate(publication).model_copy(
            update={"is_outdated": service.is_outdated(publication)}
        )
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AQSI publication not found.") from exc


@router.get("/{variant_id}/attempts", response_model=list[PublicationAttemptRead])
def list_attempts(
    variant_id: UUIDv7,
    service: Annotated[AqsiPublicationService, Depends(get_aqsi_publication_service)],
) -> list[PublicationAttemptRead]:
    """Return attributed AQSI attempt history newest first."""
    try:
        return [
            PublicationAttemptRead.model_validate(attempt)
            for attempt in service.list_attempts(variant_id)
        ]
    except PublicationNotFoundError as exc:
        raise HTTPException(status_code=404, detail="AQSI publication not found.") from exc

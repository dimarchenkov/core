from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.catalog.service import CatalogProductCategoryError, CatalogVariantProductError
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.intake.schemas import IntakeCreate, IntakeRead
from core.intake.service import IntakeService
from core.media.service import (
    ImageLinkEntityError,
    ImageLinkPrimaryConflictError,
    ImageNotFoundError,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/intake",
    tags=["intake"],
    dependencies=[Depends(get_current_user)],
)


def _actor_id(current_user: User | None) -> UUIDv7 | None:
    """Return an actor id while allowing existing system-operation test overrides."""
    return current_user.id if current_user is not None else None


def get_intake_service(session: Annotated[Session, Depends(get_session)]) -> IntakeService:
    """Provide intake service instances for route handlers."""
    return IntakeService(session)


@router.post("", response_model=IntakeRead, status_code=status.HTTP_201_CREATED)
def create_intake(
    data: IntakeCreate,
    service: Annotated[IntakeService, Depends(get_intake_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeRead:
    """Create a product, variant, SKU, and primary image link in one transaction."""
    try:
        return service.create_intake(data, actor_id=_actor_id(current_user))
    except CatalogProductCategoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake category is invalid.",
        ) from exc
    except CatalogVariantProductError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake product is invalid.",
        ) from exc
    except (ImageNotFoundError, ImageLinkEntityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake image is invalid.",
        ) from exc
    except ImageLinkPrimaryConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant already has a primary image.",
        ) from exc

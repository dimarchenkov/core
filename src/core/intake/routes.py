from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.catalog.service import CatalogProductCategoryError, CatalogVariantProductError
from core.database import get_session
from core.intake.schemas import IntakeCreate, IntakeRead
from core.intake.service import IntakeService
from core.media.service import (
    ImageLinkEntityError,
    ImageLinkPrimaryConflictError,
    ImageNotFoundError,
)

router = APIRouter(prefix="/api/intake", tags=["intake"])


def get_intake_service(session: Annotated[Session, Depends(get_session)]) -> IntakeService:
    """Provide intake service instances for route handlers."""
    return IntakeService(session)


@router.post("", response_model=IntakeRead, status_code=status.HTTP_201_CREATED)
def create_intake(
    data: IntakeCreate,
    service: Annotated[IntakeService, Depends(get_intake_service)],
) -> IntakeRead:
    """Create a product, variant, SKU, and primary image link in one transaction."""
    try:
        return service.create_intake(data)
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

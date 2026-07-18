from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.labels.service import (
    LabelVariantNotFoundError,
    LabelVariantNotReadyError,
    VariantLabelService,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/labels/variants",
    tags=["labels"],
    dependencies=[Depends(get_current_user)],
)


def get_variant_label_service(
    session: Annotated[Session, Depends(get_session)],
) -> VariantLabelService:
    """Provide label service instances for route handlers."""
    return VariantLabelService(session)


@router.get("/{variant_id}/58x40.pdf", response_class=Response)
def generate_58x40_label(
    variant_id: UUIDv7,
    service: Annotated[VariantLabelService, Depends(get_variant_label_service)],
) -> Response:
    """Return one printer-independent 58 x 40 mm PDF product label."""
    try:
        content = service.generate_58x40(variant_id)
    except LabelVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog variant not found.",
        ) from exc
    except LabelVariantNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Variant is not ready for sale.",
                "missing_requirements": exc.missing_requirements,
            },
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{variant_id}-58x40.pdf"'},
    )

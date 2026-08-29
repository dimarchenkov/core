from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.labels.renderer import LabelProfile
from core.labels.service import (
    LabelRentalAssetNotFoundError,
    LabelVariantNotFoundError,
    LabelVariantNotReadyError,
    RentalAssetLabelService,
    VariantLabelService,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/labels/variants",
    tags=["labels"],
    dependencies=[Depends(get_current_user)],
)

rental_asset_router = APIRouter(
    prefix="/api/labels/rental-assets",
    tags=["labels", "rental-assets"],
    dependencies=[Depends(get_current_user)],
)


def get_variant_label_service(
    session: Annotated[Session, Depends(get_session)],
) -> VariantLabelService:
    """Provide label service instances for route handlers."""
    return VariantLabelService(session)


def get_rental_asset_label_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalAssetLabelService:
    """Provide inventory-label services at the authenticated Rental boundary."""
    return RentalAssetLabelService(session)


@router.get("/{variant_id}/{profile}.pdf", response_class=Response)
def generate_label(
    variant_id: UUIDv7,
    profile: LabelProfile,
    service: Annotated[VariantLabelService, Depends(get_variant_label_service)],
    dpi: int = 203,
) -> Response:
    """Return one exact-size vector PDF for a supported product-label profile."""
    try:
        content = service.generate(variant_id, profile, dpi=dpi)
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
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{variant_id}-{profile.value}.pdf"'},
    )


@rental_asset_router.get("/{asset_id}/{profile}.pdf", response_class=Response)
def generate_rental_asset_label(
    asset_id: UUIDv7,
    profile: LabelProfile,
    service: Annotated[RentalAssetLabelService, Depends(get_rental_asset_label_service)],
    dpi: int = 203,
) -> Response:
    """Return one exact-size Code 128 inventory label without price requirements."""
    try:
        content = service.generate(asset_id, profile, dpi=dpi)
    except LabelRentalAssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{asset_id}-{profile.value}.pdf"'},
    )

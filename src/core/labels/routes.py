from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.labels.printing import (
    LabelPrinterUnavailableError,
    LabelPrintFailedError,
    VariantLabelPrintService,
)
from core.labels.renderer import LabelProfile
from core.labels.service import (
    LabelRentalAssetNotFoundError,
    LabelVariantNotFoundError,
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


def get_variant_label_print_service(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VariantLabelPrintService:
    """Provide the shared Catalog/Intake direct-print application service."""
    return VariantLabelPrintService(VariantLabelService(session), settings)


@router.get("/print-capability")
def get_print_capability(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    """Explain whether this backend runtime can submit a direct CUPS print job."""
    command_available = shutil.which(settings.label_printer_command) is not None
    printer_configured = settings.label_printer_name is not None
    return {
        "available": command_available and printer_configured,
        "command_available": command_available,
        "printer_configured": printer_configured,
        "printer_name": settings.label_printer_name,
        "fallback": "pdf",
    }


@router.get("/{variant_id}/{profile}.pdf", response_class=Response)
def generate_label(
    variant_id: UUIDv7,
    profile: LabelProfile,
    service: Annotated[VariantLabelService, Depends(get_variant_label_service)],
    dpi: int = 203,
    quantity: Annotated[int, Query(ge=1, le=500)] = 1,
) -> Response:
    """Return one exact-size vector PDF for a supported product-label profile."""
    try:
        content = service.generate(variant_id, profile, dpi=dpi, quantity=quantity)
    except LabelVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog variant not found.",
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


@router.post("/{variant_id}/{profile}/print")
def print_label(
    variant_id: UUIDv7,
    profile: LabelProfile,
    service: Annotated[VariantLabelPrintService, Depends(get_variant_label_print_service)],
    quantity: Annotated[int, Query(ge=1, le=500)] = 1,
) -> dict[str, object]:
    """Submit labels to the configured system queue without changing business facts."""
    try:
        result = service.print(variant_id, quantity=quantity, profile=profile)
    except LabelVariantNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Catalog variant not found.") from exc
    except LabelPrinterUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (LabelPrintFailedError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "printer_name": result.printer_name,
        "quantity": result.quantity,
        "job_id": result.job_id,
    }


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

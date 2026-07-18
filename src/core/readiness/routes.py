from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.readiness.schemas import ReadyForSaleRead
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/readiness/variants",
    tags=["readiness"],
    dependencies=[Depends(get_current_user)],
)


def get_ready_for_sale_service(
    session: Annotated[Session, Depends(get_session)],
) -> ReadyForSaleService:
    """Provide readiness service instances for route handlers."""
    return ReadyForSaleService(session)


@router.get("/{variant_id}/ready-for-sale", response_model=ReadyForSaleRead)
def check_ready_for_sale(
    variant_id: UUIDv7,
    service: Annotated[ReadyForSaleService, Depends(get_ready_for_sale_service)],
) -> ReadyForSaleRead:
    """Return derived sale readiness and all missing requirements."""
    try:
        return service.check_variant(variant_id)
    except ReadinessVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog variant not found.",
        ) from exc

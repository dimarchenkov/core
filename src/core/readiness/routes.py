from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.read_service import ReadyForSaleReadService
from core.readiness.schemas import ReadyForSaleAttentionPage, ReadyForSaleRead
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/readiness",
    tags=["readiness"],
    dependencies=[Depends(get_current_user)],
)


def get_ready_for_sale_service(
    session: Annotated[Session, Depends(get_session)],
) -> ReadyForSaleService:
    """Provide readiness service instances for route handlers."""
    return ReadyForSaleService(session)


def get_ready_for_sale_read_service(
    session: Annotated[Session, Depends(get_session)],
) -> ReadyForSaleReadService:
    """Provide derived Ready for Sale queue projections."""
    return ReadyForSaleReadService(session)


@router.get("/attention", response_model=ReadyForSaleAttentionPage)
def list_ready_for_sale_attention(
    service: Annotated[ReadyForSaleReadService, Depends(get_ready_for_sale_read_service)],
    requirement: ReadyForSaleRequirement | None = None,
    search: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReadyForSaleAttentionPage:
    """Return the current employee queue of Variants requiring sale preparation."""
    return service.list_attention(
        requirement=requirement,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/variants/{variant_id}/ready-for-sale", response_model=ReadyForSaleRead)
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

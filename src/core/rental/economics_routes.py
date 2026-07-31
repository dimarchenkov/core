from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.rental.economics_schemas import (
    RentalAssetEconomicsRead,
    RentalProductEconomicsRead,
    RentalVariantEconomicsRead,
)
from core.rental.economics_service import RentalEconomicsNotFoundError, RentalEconomicsService
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/operations/rental/economics",
    tags=["operations"],
    dependencies=[Depends(get_current_user)],
)


def get_economics_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalEconomicsService:
    """Provide read-only rental economics projections."""
    return RentalEconomicsService(session)


@router.get("/assets/{asset_id}", response_model=RentalAssetEconomicsRead)
def get_asset_economics(
    asset_id: UUIDv7, service: Annotated[RentalEconomicsService, Depends(get_economics_service)]
) -> RentalAssetEconomicsRead:
    """Return computed economics for one physical asset."""
    try:
        return service.get_asset(asset_id)
    except RentalEconomicsNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.get("/variants/{variant_id}", response_model=RentalVariantEconomicsRead)
def get_variant_economics(
    variant_id: UUIDv7, service: Annotated[RentalEconomicsService, Depends(get_economics_service)]
) -> RentalVariantEconomicsRead:
    """Return aggregated economics for one Variant."""
    try:
        return service.get_variant(variant_id)
    except RentalEconomicsNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Variant not found.") from exc


@router.get("/products/{product_id}", response_model=RentalProductEconomicsRead)
def get_product_economics(
    product_id: UUIDv7, service: Annotated[RentalEconomicsService, Depends(get_economics_service)]
) -> RentalProductEconomicsRead:
    """Return aggregated economics for one Product."""
    return service.get_product(product_id)

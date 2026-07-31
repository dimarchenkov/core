from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.rental.operations_read_service import (
    OperationsProductNotFoundError,
    RentalOperationsReadService,
)
from core.rental.operations_schemas import (
    CatalogOperationsFilter,
    CatalogProductOperationsDetail,
    CatalogProductOperationsRead,
    RentalAssetOperationsFilter,
    RentalAssetOperationsRead,
    RentalAssetOperationsSort,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/operations",
    tags=["operations"],
    dependencies=[Depends(get_current_user)],
)


def get_operations_read_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalOperationsReadService:
    """Provide read-only Catalog and Rental operational projections."""
    return RentalOperationsReadService(session)


@router.get("/catalog/products", response_model=list[CatalogProductOperationsRead])
def list_operational_products(
    service: Annotated[RentalOperationsReadService, Depends(get_operations_read_service)],
    query: Annotated[str | None, Query(max_length=255)] = None,
    product_filter: CatalogOperationsFilter = CatalogOperationsFilter.ALL,
) -> list[CatalogProductOperationsRead]:
    """List products with rental counts for the first-party client."""
    return service.list_products(query, product_filter=product_filter)


@router.get("/catalog/products/{product_id}", response_model=CatalogProductOperationsDetail)
def get_operational_product(
    product_id: UUIDv7,
    service: Annotated[RentalOperationsReadService, Depends(get_operations_read_service)],
) -> CatalogProductOperationsDetail:
    """Return one product card with variants and physical assets."""
    try:
        return service.get_product(product_id)
    except OperationsProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        ) from exc


@router.get("/rental/assets", response_model=list[RentalAssetOperationsRead])
def list_operational_assets(
    service: Annotated[RentalOperationsReadService, Depends(get_operations_read_service)],
    query: Annotated[str | None, Query(max_length=255)] = None,
    asset_filter: RentalAssetOperationsFilter = RentalAssetOperationsFilter.ALL,
    sort: RentalAssetOperationsSort = RentalAssetOperationsSort.ASSET_NUMBER,
) -> list[RentalAssetOperationsRead]:
    """List RentalAssets with filters, sorting, and current-order links."""
    return service.list_assets(query, asset_filter=asset_filter, sort=sort)

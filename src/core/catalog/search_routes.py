from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.catalog.schemas import CatalogProductSearchPage, CatalogVariantSearchPage
from core.catalog.search import CatalogSearchService
from core.database import get_session
from core.identity.dependencies import get_current_user

router = APIRouter(
    prefix="/api/catalog/search",
    tags=["catalog"],
    dependencies=[Depends(get_current_user)],
)


def get_catalog_search_service(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogSearchService:
    """Provide the shared Catalog query service."""
    return CatalogSearchService(session)


@router.get("/variants", response_model=CatalogVariantSearchPage)
def search_variants(
    service: Annotated[CatalogSearchService, Depends(get_catalog_search_service)],
    query: Annotated[str, Query(min_length=1, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=20)] = 12,
) -> CatalogVariantSearchPage:
    """Return existing Variants for repeat Intake."""
    return service.search_variants(query, limit=limit)


@router.get("/products", response_model=CatalogProductSearchPage)
def search_products(
    service: Annotated[CatalogSearchService, Depends(get_catalog_search_service)],
    query: Annotated[str, Query(min_length=1, max_length=255)],
    limit: Annotated[int, Query(ge=1, le=20)] = 12,
) -> CatalogProductSearchPage:
    """Return deduplicated parent Products for new-Variant Intake."""
    return service.search_products(query, limit=limit)

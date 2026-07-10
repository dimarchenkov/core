from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.catalog.schemas import (
    CatalogProductCreate,
    CatalogProductRead,
    CatalogProductUpdate,
    CatalogVariantCreate,
    CatalogVariantRead,
    CatalogVariantUpdate,
    CategoryCreate,
    CategoryRead,
    CategoryUpdate,
)
from core.catalog.service import (
    CatalogProductCategoryError,
    CatalogProductNotFoundError,
    CatalogProductService,
    CatalogProductSlugAlreadyExistsError,
    CatalogVariantNotFoundError,
    CatalogVariantProductError,
    CatalogVariantService,
    CategoryNotFoundError,
    CategoryParentError,
    CategoryService,
    CategorySlugAlreadyExistsError,
)
from core.database import get_session
from core.shared.db import UUIDv7

router = APIRouter(prefix="/api/catalog/categories", tags=["catalog"])
product_router = APIRouter(prefix="/api/catalog/products", tags=["catalog"])
variant_router = APIRouter(prefix="/api/catalog/variants", tags=["catalog"])


def get_category_service(session: Annotated[Session, Depends(get_session)]) -> CategoryService:
    """Provide category service instances for route handlers."""
    return CategoryService(session)


def get_catalog_product_service(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogProductService:
    """Provide catalog product service instances for route handlers."""
    return CatalogProductService(session)


def get_catalog_variant_service(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogVariantService:
    """Provide catalog variant service instances for route handlers."""
    return CatalogVariantService(session)


@router.get("", response_model=list[CategoryRead])
def list_categories(
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> Sequence[Category]:
    """Return all active catalog categories."""
    return service.list_categories()


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    data: CategoryCreate,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> Category:
    """Create a catalog category."""
    try:
        return service.create_category(data)
    except CategorySlugAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category slug already exists.",
        ) from exc
    except CategoryParentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parent category is invalid.",
        ) from exc


@router.get("/{category_id}", response_model=CategoryRead)
def get_category(
    category_id: UUIDv7,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> Category:
    """Return one catalog category by id."""
    try:
        return service.get_category(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found.",
        ) from exc


@router.patch("/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: UUIDv7,
    data: CategoryUpdate,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> Category:
    """Update a catalog category."""
    try:
        return service.update_category(category_id, data)
    except CategoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found.",
        ) from exc
    except CategorySlugAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category slug already exists.",
        ) from exc
    except CategoryParentError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Parent category is invalid.",
        ) from exc


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: UUIDv7,
    service: Annotated[CategoryService, Depends(get_category_service)],
) -> Response:
    """Soft-delete a catalog category."""
    try:
        service.delete_category(category_id)
    except CategoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@product_router.get("", response_model=list[CatalogProductRead])
def list_products(
    service: Annotated[CatalogProductService, Depends(get_catalog_product_service)],
) -> Sequence[CatalogProduct]:
    """Return all non-deleted catalog products."""
    return service.list_products()


@product_router.post("", response_model=CatalogProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    data: CatalogProductCreate,
    service: Annotated[CatalogProductService, Depends(get_catalog_product_service)],
) -> CatalogProduct:
    """Create a catalog product."""
    try:
        return service.create_product(data)
    except CatalogProductSlugAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product slug already exists.",
        ) from exc
    except CatalogProductCategoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product category is invalid.",
        ) from exc


@product_router.get("/{product_id}", response_model=CatalogProductRead)
def get_product(
    product_id: UUIDv7,
    service: Annotated[CatalogProductService, Depends(get_catalog_product_service)],
) -> CatalogProduct:
    """Return one catalog product by id."""
    try:
        return service.get_product(product_id)
    except CatalogProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        ) from exc


@product_router.patch("/{product_id}", response_model=CatalogProductRead)
def update_product(
    product_id: UUIDv7,
    data: CatalogProductUpdate,
    service: Annotated[CatalogProductService, Depends(get_catalog_product_service)],
) -> CatalogProduct:
    """Update a catalog product."""
    try:
        return service.update_product(product_id, data)
    except CatalogProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        ) from exc
    except CatalogProductSlugAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product slug already exists.",
        ) from exc
    except CatalogProductCategoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product category is invalid.",
        ) from exc


@product_router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: UUIDv7,
    service: Annotated[CatalogProductService, Depends(get_catalog_product_service)],
) -> Response:
    """Soft-delete a catalog product."""
    try:
        service.delete_product(product_id)
    except CatalogProductNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@variant_router.get("", response_model=list[CatalogVariantRead])
def list_variants(
    service: Annotated[CatalogVariantService, Depends(get_catalog_variant_service)],
) -> Sequence[CatalogVariant]:
    """Return all non-deleted catalog variants."""
    return service.list_variants()


@variant_router.post("", response_model=CatalogVariantRead, status_code=status.HTTP_201_CREATED)
def create_variant(
    data: CatalogVariantCreate,
    service: Annotated[CatalogVariantService, Depends(get_catalog_variant_service)],
) -> CatalogVariant:
    """Create a catalog variant with a system-generated SKU."""
    try:
        return service.create_variant(data)
    except CatalogVariantProductError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Variant product is invalid.",
        ) from exc


@variant_router.get("/{variant_id}", response_model=CatalogVariantRead)
def get_variant(
    variant_id: UUIDv7,
    service: Annotated[CatalogVariantService, Depends(get_catalog_variant_service)],
) -> CatalogVariant:
    """Return one catalog variant by id."""
    try:
        return service.get_variant(variant_id)
    except CatalogVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Variant not found.",
        ) from exc


@variant_router.patch("/{variant_id}", response_model=CatalogVariantRead)
def update_variant(
    variant_id: UUIDv7,
    data: CatalogVariantUpdate,
    service: Annotated[CatalogVariantService, Depends(get_catalog_variant_service)],
) -> CatalogVariant:
    """Update a catalog variant without changing its SKU."""
    try:
        return service.update_variant(variant_id, data)
    except CatalogVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Variant not found.",
        ) from exc
    except CatalogVariantProductError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Variant product is invalid.",
        ) from exc


@variant_router.delete("/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    variant_id: UUIDv7,
    service: Annotated[CatalogVariantService, Depends(get_catalog_variant_service)],
) -> Response:
    """Soft-delete a catalog variant."""
    try:
        service.delete_variant(variant_id)
    except CatalogVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Variant not found.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)

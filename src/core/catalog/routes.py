from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.catalog.models import Category
from core.catalog.schemas import CategoryCreate, CategoryRead, CategoryUpdate
from core.catalog.service import (
    CategoryNotFoundError,
    CategoryParentError,
    CategoryService,
    CategorySlugAlreadyExistsError,
)
from core.database import get_session
from core.shared.db import UUIDv7

router = APIRouter(prefix="/api/catalog/categories", tags=["catalog"])


def get_category_service(session: Annotated[Session, Depends(get_session)]) -> CategoryService:
    """Provide category service instances for route handlers."""
    return CategoryService(session)


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

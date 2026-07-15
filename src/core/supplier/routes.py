from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.shared.db import UUIDv7
from core.supplier.models import Supplier
from core.supplier.schemas import SupplierCreate, SupplierRead, SupplierUpdate
from core.supplier.service import (
    SupplierNameRequiredError,
    SupplierNotFoundError,
    SupplierService,
)

router = APIRouter(
    prefix="/api/purchasing/suppliers",
    tags=["purchasing"],
    dependencies=[Depends(get_current_user)],
)


def get_supplier_service(session: Annotated[Session, Depends(get_session)]) -> SupplierService:
    """Provide supplier service instances for purchasing route handlers."""
    return SupplierService(session)


@router.get("", response_model=list[SupplierRead])
def list_suppliers(
    service: Annotated[SupplierService, Depends(get_supplier_service)],
) -> Sequence[Supplier]:
    """Return suppliers that have not been archived."""
    return service.list_suppliers()


@router.post("", response_model=SupplierRead, status_code=status.HTTP_201_CREATED)
def register_supplier(
    data: SupplierCreate,
    service: Annotated[SupplierService, Depends(get_supplier_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Supplier:
    """Register a supplier and assign a stable system code."""
    try:
        return service.register_supplier(data, actor_id=current_user.id)
    except SupplierNameRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supplier name is required.",
        ) from exc


@router.get("/{supplier_id}", response_model=SupplierRead)
def get_supplier(
    supplier_id: UUIDv7,
    service: Annotated[SupplierService, Depends(get_supplier_service)],
) -> Supplier:
    """Return one non-archived supplier by identifier."""
    try:
        return service.get_supplier(supplier_id)
    except SupplierNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found.",
        ) from exc


@router.patch("/{supplier_id}", response_model=SupplierRead)
def update_supplier(
    supplier_id: UUIDv7,
    data: SupplierUpdate,
    service: Annotated[SupplierService, Depends(get_supplier_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Supplier:
    """Update mutable supplier fields while preserving its stable code."""
    try:
        return service.update_supplier(supplier_id, data, actor_id=current_user.id)
    except SupplierNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found.",
        ) from exc
    except SupplierNameRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Supplier name is required.",
        ) from exc


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_supplier(
    supplier_id: UUIDv7,
    service: Annotated[SupplierService, Depends(get_supplier_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Archive a supplier without removing its future purchasing history."""
    try:
        service.archive_supplier(supplier_id, actor_id=current_user.id)
    except SupplierNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Supplier not found.",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)

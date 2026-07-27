from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from core.customers.customer import Customer
from core.customers.enums import CustomerStatus
from core.customers.exceptions import (
    CustomerNotFoundError,
    CustomerStateError,
    CustomerValidationError,
)
from core.customers.schemas import CustomerCreate, CustomerRead, CustomerUpdate
from core.customers.service import CustomerService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/customers",
    tags=["customers"],
    dependencies=[Depends(get_current_user)],
)


def get_customer_service(session: Annotated[Session, Depends(get_session)]) -> CustomerService:
    """Provide Customer services for authenticated request handlers."""
    return CustomerService(session)


@router.get("", response_model=list[CustomerRead])
def search_customers(
    service: Annotated[CustomerService, Depends(get_customer_service)],
    query: Annotated[str | None, Query(max_length=255)] = None,
    customer_status: CustomerStatus | None = None,
) -> Sequence[Customer]:
    """Search customers by number, phone, or name."""
    return service.search_customers(query, status=customer_status)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    data: CustomerCreate,
    service: Annotated[CustomerService, Depends(get_customer_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Customer:
    """Register a customer and assign a stable number."""
    try:
        return service.create_customer(data, actor_id=current_user.id)
    except CustomerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__) from exc


@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: UUIDv7,
    service: Annotated[CustomerService, Depends(get_customer_service)],
) -> Customer:
    """Return one customer by technical identifier."""
    try:
        return service.get_customer(customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found.") from exc


@router.patch("/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: UUIDv7,
    data: CustomerUpdate,
    service: Annotated[CustomerService, Depends(get_customer_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Customer:
    """Change current customer contacts without rewriting rental snapshots."""
    try:
        return service.update_customer(customer_id, data, actor_id=current_user.id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found.") from exc
    except CustomerValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or exc.__class__.__name__) from exc


@router.post("/{customer_id}/activate", response_model=CustomerRead)
def activate_customer(
    customer_id: UUIDv7,
    service: Annotated[CustomerService, Depends(get_customer_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Customer:
    """Allow an inactive customer to enter new rentals."""
    return _change_customer_status(
        customer_id,
        service=service,
        current_user=current_user,
        activate=True,
    )


@router.post("/{customer_id}/deactivate", response_model=CustomerRead)
def deactivate_customer(
    customer_id: UUIDv7,
    service: Annotated[CustomerService, Depends(get_customer_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Customer:
    """Exclude a customer from new rentals without deleting history."""
    return _change_customer_status(
        customer_id,
        service=service,
        current_user=current_user,
        activate=False,
    )


def _change_customer_status(
    customer_id: UUIDv7,
    *,
    service: CustomerService,
    current_user: User,
    activate: bool,
) -> Customer:
    """Translate lifecycle errors into stable HTTP responses."""
    try:
        if activate:
            return service.activate_customer(customer_id, actor_id=current_user.id)
        return service.deactivate_customer(customer_id, actor_id=current_user.id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found.") from exc
    except CustomerStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

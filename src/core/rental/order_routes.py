from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.rental.exceptions import RentalDomainError
from core.rental.order import RentalOrder
from core.rental.order_enums import RentalOrderStatus
from core.rental.order_exceptions import RentalOrderDomainError
from core.rental.order_schemas import (
    RentalOrderCreate,
    RentalOrderItemCreate,
    RentalOrderItemReturn,
    RentalOrderRead,
    RentalOrderUpdate,
)
from core.rental.order_service import (
    RentalAssetNotFoundError,
    RentalAssetVariantNotFoundError,
    RentalCustomerUnavailableError,
    RentalOrderNotFoundError,
    RentalOrderService,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/rental/orders",
    tags=["rental-orders"],
    dependencies=[Depends(get_current_user)],
)


def get_rental_order_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalOrderService:
    """Provide RentalOrder workflows for authenticated request handlers."""
    return RentalOrderService(session)


@router.post("", response_model=RentalOrderRead, status_code=status.HTTP_201_CREATED)
def create_rental_order(
    data: RentalOrderCreate,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Open an empty draft and capture current customer details."""
    return _execute(
        lambda: service.create_draft(data, actor_id=current_user.id),
        not_found_customer=True,
    )


@router.get("", response_model=list[RentalOrderRead])
def search_rental_orders(
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    query: Annotated[str | None, Query(max_length=255)] = None,
    order_status: RentalOrderStatus | None = None,
    customer_id: UUIDv7 | None = None,
) -> Sequence[RentalOrder]:
    """Search current orders by business number or customer snapshot."""
    return service.search(query, status=order_status, customer_id=customer_id)


@router.get("/{order_id}", response_model=RentalOrderRead)
def get_rental_order(
    order_id: UUIDv7,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
) -> RentalOrder:
    """Return one fully rehydrated aggregate."""
    return _execute(lambda: service.get(order_id))


@router.patch("/{order_id}", response_model=RentalOrderRead)
def update_rental_order(
    order_id: UUIDv7,
    data: RentalOrderUpdate,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Change mutable details of a draft order."""
    return _execute(lambda: service.update_draft(order_id, data, actor_id=current_user.id))


@router.post("/{order_id}/items", response_model=RentalOrderRead)
def add_rental_order_item(
    order_id: UUIDv7,
    data: RentalOrderItemCreate,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Attach one available physical asset to a draft."""
    return _execute(lambda: service.add_item(order_id, data, actor_id=current_user.id))


@router.delete("/{order_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rental_order_item(
    order_id: UUIDv7,
    item_id: UUIDv7,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Remove one prepared item from a draft."""
    _execute(lambda: service.remove_item(order_id, item_id, actor_id=current_user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{order_id}/issue", response_model=RentalOrderRead)
def issue_rental_order(
    order_id: UUIDv7,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Issue all order items and their physical assets atomically."""
    return _execute(lambda: service.issue(order_id, actor_id=current_user.id))


@router.post("/{order_id}/cancel", response_model=RentalOrderRead)
def cancel_rental_order(
    order_id: UUIDv7,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Cancel one unissued draft."""
    return _execute(lambda: service.cancel(order_id, actor_id=current_user.id))


@router.post("/{order_id}/items/{item_id}/return", response_model=RentalOrderRead)
def return_rental_order_item(
    order_id: UUIDv7,
    item_id: UUIDv7,
    data: RentalOrderItemReturn,
    service: Annotated[RentalOrderService, Depends(get_rental_order_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalOrder:
    """Return one item and update its physical asset in the same transaction."""
    return _execute(
        lambda: service.return_item(order_id, item_id, data, actor_id=current_user.id)
    )


def _execute(
    command: Callable[[], RentalOrder],
    *,
    not_found_customer: bool = False,
) -> RentalOrder:
    """Translate application and domain failures into stable HTTP responses."""
    try:
        return command()
    except RentalOrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental order not found.") from exc
    except RentalAssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc
    except RentalAssetVariantNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Rental asset variant not found.") from exc
    except RentalCustomerUnavailableError as exc:
        code = 404 if not_found_customer else 409
        raise HTTPException(status_code=code, detail="Customer is unavailable.") from exc
    except (RentalOrderDomainError, RentalDomainError) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc) or exc.__class__.__name__,
        ) from exc

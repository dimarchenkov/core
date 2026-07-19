from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.schemas import PriceCreate, PriceRead
from core.pricing.service import (
    CurrentPriceNotFoundError,
    PriceService,
    PriceVariantNotFoundError,
    UnsupportedCurrencyError,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/pricing/variants",
    tags=["pricing"],
    dependencies=[Depends(get_current_user)],
)


def get_price_service(session: Annotated[Session, Depends(get_session)]) -> PriceService:
    """Provide price service instances for route handlers."""
    return PriceService(session)


@router.post(
    "/{variant_id}/prices",
    response_model=PriceRead,
    status_code=status.HTTP_201_CREATED,
)
def set_price(
    variant_id: UUIDv7,
    data: PriceCreate,
    service: Annotated[PriceService, Depends(get_price_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Price:
    """Append one price fact to a sellable variant's history."""
    try:
        price = service.set_price(variant_id, data, actor_id=current_user.id)
        session.commit()
        session.refresh(price)
        return price
    except PriceVariantNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active catalog variant not found.",
        ) from exc
    except UnsupportedCurrencyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only RUB prices are currently supported.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/{variant_id}/prices/current", response_model=PriceRead)
def get_current_price(
    variant_id: UUIDv7,
    price_type: PriceType,
    service: Annotated[PriceService, Depends(get_price_service)],
) -> Price:
    """Return the current applicable price of one type for a variant."""
    try:
        return service.get_current_price(variant_id, price_type)
    except PriceVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog variant not found.",
        ) from exc
    except CurrentPriceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Current price not found.",
        ) from exc


@router.get("/{variant_id}/prices", response_model=list[PriceRead])
def get_price_history(
    variant_id: UUIDv7,
    service: Annotated[PriceService, Depends(get_price_service)],
    price_type: PriceType | None = None,
) -> Sequence[Price]:
    """Return immutable price history for a variant."""
    try:
        return service.get_price_history(variant_id, price_type=price_type)
    except PriceVariantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Catalog variant not found.",
        ) from exc

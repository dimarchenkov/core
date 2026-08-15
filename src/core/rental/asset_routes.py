from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.rental.asset_schemas import RentalAssetRead
from core.rental.enums import RentalAvailability
from core.rental.repository import RentalAssetRepository

router = APIRouter(
    prefix="/api/rental/assets",
    tags=["rental-assets"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[RentalAssetRead])
def search_rental_assets(
    session: Annotated[Session, Depends(get_session)],
    query: Annotated[str | None, Query(max_length=255)] = None,
    availability: RentalAvailability | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[RentalAssetRead]:
    """Find physical assets by number, product, variant, or SKU."""
    rows = RentalAssetRepository(session).search(
        query,
        availability=availability,
        limit=limit,
    )
    prices = PriceRepository(session)
    now = datetime.now(UTC)
    return [
        RentalAssetRead(
            id=record.id,
            asset_number=record.asset_number,
            variant_id=record.variant_id,
            product_title=product_title,
            variant_title=variant_title,
            condition=record.condition,
            availability=record.availability,
            suggested_rental_price=(
                rental_price.amount if (rental_price := prices.get_current(
                    record.variant_id, PriceType.RENTAL, at=now
                )) is not None else None
            ),
            recommended_deposit=(
                deposit.amount if (deposit := prices.get_current(
                    record.variant_id, PriceType.RENTAL_DEPOSIT, at=now
                )) is not None else None
            ),
        )
        for record, product_title, variant_title in rows
    ]

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.rental.allocation_schemas import (
    InventoryAdjustmentCreate,
    InventoryAdjustmentRead,
    RentalAllocationCreate,
    RentalAllocationRead,
)
from core.rental.allocation_workflow import (
    InventoryAdjustmentBalanceError,
    InventoryAdjustmentForbiddenError,
    InventoryRentalWorkflow,
    RentalAllocationError,
)
from core.rental.exceptions import InvalidRentalStateError
from core.shared.db import UUIDv7

router = APIRouter(prefix="/api/operations", tags=["operations"])


def workflow(session: Annotated[Session, Depends(get_session)]) -> InventoryRentalWorkflow:
    """Provide the Inventory/Rental transaction coordinator."""
    return InventoryRentalWorkflow(session)


@router.post("/catalog/variants/{variant_id}/allocate-rental", response_model=RentalAllocationRead)
def allocate_rental(
    variant_id: UUIDv7,
    data: RentalAllocationCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[InventoryRentalWorkflow, Depends(workflow)],
) -> RentalAllocationRead:
    """Allocate existing ordinary stock to individually numbered RentalAssets."""
    try:
        assets = service.allocate(variant_id, data.quantity, actor_id=current_user.id)
    except RentalAllocationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Insufficient ordinary inventory.") from exc
    return RentalAllocationRead(
        asset_ids=[asset.id for asset in assets],
        asset_numbers=[asset.asset_number for asset in assets],
    )


@router.post("/rental/assets/{asset_id}/withdraw-for-sale", status_code=status.HTTP_204_NO_CONTENT)
def withdraw_for_sale(
    asset_id: UUIDv7,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[InventoryRentalWorkflow, Depends(workflow)],
) -> None:
    """Return an eligible physical asset to ordinary inventory."""
    try:
        service.withdraw(asset_id, actor_id=current_user.id)
    except (RentalAllocationError, InvalidRentalStateError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Asset cannot be withdrawn.") from exc


@router.post(
    "/catalog/variants/{variant_id}/inventory-adjustments", response_model=InventoryAdjustmentRead
)
def adjust_inventory(
    variant_id: UUIDv7,
    data: InventoryAdjustmentCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[InventoryRentalWorkflow, Depends(workflow)],
) -> InventoryAdjustmentRead:
    """Append an administrator-only manual stock correction."""
    try:
        movement, balance = service.adjust(
            variant_id,
            data.quantity_delta,
            data.reason,
            data.comment,
            actor_id=current_user.id,
            is_admin=current_user.is_admin,
        )
    except InventoryAdjustmentForbiddenError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Administrator permission required."
        ) from exc
    except InventoryAdjustmentBalanceError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Adjustment conflicts with allocated rental inventory."
        ) from exc
    return InventoryAdjustmentRead(movement_id=movement.id, balance=balance)

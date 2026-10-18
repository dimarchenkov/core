from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from core.config import get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.media.inspection import UnsupportedImageError
from core.media.service import ImageFileTooLargeError, ImageService
from core.media.storage import LocalImageStorage
from core.rental.lifecycle_schemas import (
    ConditionPhotoRead,
    ConditionPhotoStage,
    CustomerRentalHistoryRead,
    DamageCreate,
    DamageRead,
    MaintenanceCreate,
    MaintenanceRead,
    RentalAssetComponentCreate,
    RentalAssetComponentOrder,
    RentalAssetComponentRead,
    RentalAssetComponentUpdate,
    RentalAssetIdentificationUpdate,
    RentalAssetPassportRead,
    RentalAssetSealCreate,
    RentalAssetSealRead,
    RentalAssetSealStatusUpdate,
)
from core.rental.lifecycle_service import (
    RentalComponentStateError,
    RentalLifecycleNotFoundError,
    RentalLifecycleReferenceError,
    RentalLifecycleService,
    RentalSealStateError,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/operations/rental",
    tags=["rental-lifecycle"],
    dependencies=[Depends(get_current_user)],
)


def get_lifecycle_service(
    session: Annotated[Session, Depends(get_session)],
) -> RentalLifecycleService:
    """Provide Rental lifecycle journals and history projections."""
    return RentalLifecycleService(session)


def get_lifecycle_image_service(
    session: Annotated[Session, Depends(get_session)],
) -> ImageService:
    """Provide validated source-image storage for condition evidence."""
    return ImageService(session, storage=LocalImageStorage(get_settings().storage_root))


@router.get("/assets/{asset_id}/passport", response_model=RentalAssetPassportRead)
def get_asset_passport(
    asset_id: UUIDv7,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
) -> RentalAssetPassportRead:
    """Return the complete operational passport of one physical item."""
    try:
        return service.get_asset_passport(asset_id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.patch("/assets/{asset_id}/identification", response_model=RentalAssetPassportRead)
def update_asset_identification(
    asset_id: UUIDv7,
    data: RentalAssetIdentificationUpdate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetPassportRead:
    """Set or clear an optional serial number without changing asset availability."""
    try:
        return service.update_asset_identification(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.post(
    "/assets/{asset_id}/seals",
    response_model=RentalAssetSealRead,
    status_code=status.HTTP_201_CREATED,
)
def add_asset_seal(
    asset_id: UUIDv7,
    data: RentalAssetSealCreate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetSealRead:
    """Install a new seal while retaining any replaced seal in history."""
    try:
        return service.add_seal(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.patch(
    "/assets/{asset_id}/seals/{seal_id}", response_model=RentalAssetSealRead
)
def update_asset_seal_status(
    asset_id: UUIDv7,
    seal_id: UUIDv7,
    data: RentalAssetSealStatusUpdate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetSealRead:
    """Mark one active seal removed or broken without rewriting its identity."""
    try:
        return service.update_seal_status(
            asset_id, seal_id, data, actor_id=current_user.id
        )
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental seal not found.") from exc
    except RentalSealStateError as exc:
        raise HTTPException(status_code=409, detail="Seal is no longer active.") from exc


@router.post(
    "/assets/{asset_id}/components",
    response_model=RentalAssetComponentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_asset_component(
    asset_id: UUIDv7,
    data: RentalAssetComponentCreate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetComponentRead:
    """Add one optional checklist element to current asset configuration."""
    try:
        return service.add_component(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.patch(
    "/assets/{asset_id}/components/{component_id}",
    response_model=RentalAssetComponentRead,
)
def update_asset_component(
    asset_id: UUIDv7,
    component_id: UUIDv7,
    data: RentalAssetComponentUpdate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetComponentRead:
    """Edit a component for future issues only."""
    try:
        return service.update_component(
            asset_id, component_id, data, actor_id=current_user.id
        )
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental component not found.") from exc
    except RentalComponentStateError as exc:
        raise HTTPException(status_code=409, detail="Rental component is inactive.") from exc


@router.delete(
    "/assets/{asset_id}/components/{component_id}",
    response_model=RentalAssetComponentRead,
)
def deactivate_asset_component(
    asset_id: UUIDv7,
    component_id: UUIDv7,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> RentalAssetComponentRead:
    """Deactivate a component without deleting history or old snapshots."""
    try:
        return service.deactivate_component(
            asset_id, component_id, actor_id=current_user.id
        )
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental component not found.") from exc
    except RentalComponentStateError as exc:
        raise HTTPException(status_code=409, detail="Rental component is inactive.") from exc


@router.put(
    "/assets/{asset_id}/components/order",
    response_model=list[RentalAssetComponentRead],
)
def reorder_asset_components(
    asset_id: UUIDv7,
    data: RentalAssetComponentOrder,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[RentalAssetComponentRead]:
    """Reorder every active component using one unambiguous command."""
    try:
        return service.reorder_components(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc
    except RentalComponentStateError as exc:
        raise HTTPException(
            status_code=409, detail="Component order is stale; reload the asset."
        ) from exc


@router.post(
    "/assets/{asset_id}/maintenance",
    response_model=MaintenanceRead,
    status_code=status.HTTP_201_CREATED,
)
def add_asset_maintenance(
    asset_id: UUIDv7,
    data: MaintenanceCreate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MaintenanceRead:
    """Append one service record to the physical item's journal."""
    try:
        return service.add_maintenance(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc


@router.post(
    "/assets/{asset_id}/damages",
    response_model=DamageRead,
    status_code=status.HTTP_201_CREATED,
)
def add_asset_damage(
    asset_id: UUIDv7,
    data: DamageCreate,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DamageRead:
    """Append a damage observation, optionally linked to a rental item."""
    try:
        return service.add_damage(asset_id, data, actor_id=current_user.id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc
    except RentalLifecycleReferenceError as exc:
        raise HTTPException(
            status_code=409,
            detail="Rental order item does not belong to this asset.",
        ) from exc


@router.post(
    "/assets/{asset_id}/condition-photos",
    response_model=ConditionPhotoRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_asset_condition_photo(
    asset_id: UUIDv7,
    stage: Annotated[ConditionPhotoStage, Form()],
    file: Annotated[UploadFile, File()],
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
    image_service: Annotated[ImageService, Depends(get_lifecycle_image_service)],
    current_user: Annotated[User, Depends(get_current_user)],
    order_item_id: Annotated[UUIDv7 | None, Form()] = None,
) -> ConditionPhotoRead:
    """Store one before/after condition photo without introducing a gallery."""
    content = await file.read(ImageService.max_source_size_bytes + 1)
    try:
        return service.add_condition_photo(
            asset_id,
            stage=stage,
            original_filename=file.filename or "condition-photo",
            content=content,
            image_service=image_service,
            actor_id=current_user.id,
            order_item_id=order_item_id,
        )
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Rental asset not found.") from exc
    except RentalLifecycleReferenceError as exc:
        raise HTTPException(
            status_code=409,
            detail="Rental order item does not belong to this asset.",
        ) from exc
    except ImageFileTooLargeError as exc:
        raise HTTPException(status_code=413, detail="Image file exceeds the 15 MB limit.") from exc
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.get("/customers/{customer_id}/history", response_model=CustomerRentalHistoryRead)
def get_customer_rental_history(
    customer_id: UUIDv7,
    service: Annotated[RentalLifecycleService, Depends(get_lifecycle_service)],
) -> CustomerRentalHistoryRead:
    """Return active and completed rental contracts for one customer."""
    try:
        return service.get_customer_history(customer_id)
    except RentalLifecycleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found.") from exc

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.receipt.models import Receipt, ReceiptItem
from core.receipt.posting import ReceiptItemsRequiredError, ReceiptPostingService
from core.receipt.schemas import (
    ReceiptCreate,
    ReceiptItemCreate,
    ReceiptItemRead,
    ReceiptItemUpdate,
    ReceiptRead,
    ReceiptUpdate,
)
from core.receipt.service import (
    ReceiptItemNotFoundError,
    ReceiptItemService,
    ReceiptNotDraftError,
    ReceiptNotFoundError,
    ReceiptService,
    ReceiptSupplierError,
    ReceiptVariantError,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/receipts", tags=["receipts"], dependencies=[Depends(get_current_user)]
)


def get_receipt_service(session: Annotated[Session, Depends(get_session)]) -> ReceiptService:
    """Provide receipt service instances for route handlers."""
    return ReceiptService(session)


def get_receipt_item_service(
    session: Annotated[Session, Depends(get_session)],
) -> ReceiptItemService:
    """Provide receipt item service instances for route handlers."""
    return ReceiptItemService(session)


def get_receipt_posting_service(
    session: Annotated[Session, Depends(get_session)],
) -> ReceiptPostingService:
    """Provide receipt posting service instances for route handlers."""
    return ReceiptPostingService(session)


@router.get("", response_model=list[ReceiptRead])
def list_receipts(
    service: Annotated[ReceiptService, Depends(get_receipt_service)],
) -> Sequence[Receipt]:
    """Return non-deleted supplier receipts."""
    return service.list_receipts()


@router.post("", response_model=ReceiptRead, status_code=status.HTTP_201_CREATED)
def open_receipt(
    data: ReceiptCreate,
    service: Annotated[ReceiptService, Depends(get_receipt_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Receipt:
    """Open an empty draft receipt for an active supplier."""
    try:
        return service.open_receipt(data, actor_id=current_user.id)
    except ReceiptSupplierError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Supplier is invalid."
        ) from exc


@router.post("/{receipt_id}/post", response_model=ReceiptRead, status_code=status.HTTP_200_OK)
def post_receipt(
    receipt_id: UUIDv7,
    service: Annotated[ReceiptPostingService, Depends(get_receipt_posting_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Receipt:
    """Post a validated draft receipt and create its inventory movements."""
    try:
        return service.post_receipt(receipt_id, actor_id=current_user.id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    except ReceiptSupplierError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Supplier is invalid."
        ) from exc
    except ReceiptVariantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Variant is invalid."
        ) from exc
    except ReceiptItemsRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt has no items."
        ) from exc


@router.get("/{receipt_id}", response_model=ReceiptRead)
def get_receipt(
    receipt_id: UUIDv7,
    service: Annotated[ReceiptService, Depends(get_receipt_service)],
) -> Receipt:
    """Return one non-deleted receipt by identifier."""
    try:
        return service.get_receipt(receipt_id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc


@router.patch("/{receipt_id}", response_model=ReceiptRead)
def update_draft(
    receipt_id: UUIDv7,
    data: ReceiptUpdate,
    service: Annotated[ReceiptService, Depends(get_receipt_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Receipt:
    """Update mutable fields of a draft receipt."""
    try:
        return service.update_draft(receipt_id, data, actor_id=current_user.id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    except ReceiptSupplierError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Supplier is invalid."
        ) from exc


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_draft(
    receipt_id: UUIDv7,
    service: Annotated[ReceiptService, Depends(get_receipt_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Archive a draft receipt without affecting inventory."""
    try:
        service.archive_draft(receipt_id, actor_id=current_user.id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{receipt_id}/items", response_model=list[ReceiptItemRead])
def list_items(
    receipt_id: UUIDv7,
    service: Annotated[ReceiptItemService, Depends(get_receipt_item_service)],
) -> Sequence[ReceiptItem]:
    """Return active lines for one non-deleted receipt."""
    try:
        return service.list_items(receipt_id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc


@router.post(
    "/{receipt_id}/items", response_model=ReceiptItemRead, status_code=status.HTTP_201_CREATED
)
def add_item(
    receipt_id: UUIDv7,
    data: ReceiptItemCreate,
    service: Annotated[ReceiptItemService, Depends(get_receipt_item_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ReceiptItem:
    """Add an existing active variant to a draft receipt."""
    try:
        return service.add_item(receipt_id, data, actor_id=current_user.id)
    except ReceiptNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    except ReceiptVariantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Variant is invalid."
        ) from exc


@router.patch("/{receipt_id}/items/{item_id}", response_model=ReceiptItemRead)
def update_item(
    receipt_id: UUIDv7,
    item_id: UUIDv7,
    data: ReceiptItemUpdate,
    service: Annotated[ReceiptItemService, Depends(get_receipt_item_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ReceiptItem:
    """Update one line while its receipt remains draft."""
    try:
        return service.update_item(receipt_id, item_id, data, actor_id=current_user.id)
    except (ReceiptNotFoundError, ReceiptItemNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt item not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    except ReceiptVariantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Variant is invalid."
        ) from exc


@router.delete("/{receipt_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_item(
    receipt_id: UUIDv7,
    item_id: UUIDv7,
    service: Annotated[ReceiptItemService, Depends(get_receipt_item_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Archive one draft receipt line without changing stock."""
    try:
        service.remove_item(receipt_id, item_id, actor_id=current_user.id)
    except (ReceiptNotFoundError, ReceiptItemNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Receipt item not found."
        ) from exc
    except ReceiptNotDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Receipt is not a draft."
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from core.catalog.service import CatalogProductCategoryError, CatalogVariantProductError
from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.intake.completion import (
    CompleteIntakeWorkflow,
    IntakeCompletionAbandonedError,
    IntakeCompletionIncompleteError,
    IntakeCompletionNotFoundError,
)
from core.intake.draft_service import (
    IntakeCategoryError,
    IntakeDraftWorkflow,
    IntakeItemFieldError,
    IntakeItemNotDraftError,
    IntakeItemNotFoundError,
    IntakeProductError,
    IntakeSessionNotDraftError,
    IntakeSupplierError,
    IntakeVariantError,
)
from core.intake.enums import IntakeSessionStatus
from core.intake.read_service import IntakeDraftReadService, IntakeSessionNotFoundError
from core.intake.schemas import (
    ExistingIntakeItemCreate,
    IntakeAbandon,
    IntakeCompletionRead,
    IntakeCreate,
    IntakeItemDraftRead,
    IntakeItemDraftUpdate,
    IntakeRead,
    IntakeSessionRead,
    IntakeSessionUpdate,
)
from core.intake.service import IntakeService
from core.media.inspection import UnsupportedImageError
from core.media.service import (
    ImageFileTooLargeError,
    ImageLinkEntityError,
    ImageLinkPrimaryConflictError,
    ImageNotFoundError,
    ImageService,
)
from core.media.storage import LocalImageStorage
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/intake",
    tags=["intake"],
    dependencies=[Depends(get_current_user)],
)


def _actor_id(current_user: User | None) -> UUIDv7 | None:
    """Return an actor id while allowing existing system-operation test overrides."""
    return current_user.id if current_user is not None else None


def get_intake_service(session: Annotated[Session, Depends(get_session)]) -> IntakeService:
    """Provide intake service instances for route handlers."""
    return IntakeService(session)


def get_intake_draft_workflow(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IntakeDraftWorkflow:
    """Provide resumable Intake commands with local source-image storage."""
    image_service = ImageService(session, storage=LocalImageStorage(settings.storage_root))
    return IntakeDraftWorkflow(session, image_service)


def get_intake_draft_read_service(
    session: Annotated[Session, Depends(get_session)],
) -> IntakeDraftReadService:
    """Provide owned IntakeSession projections without storage dependencies."""
    return IntakeDraftReadService(session)


def get_complete_intake_workflow(
    session: Annotated[Session, Depends(get_session)],
) -> CompleteIntakeWorkflow:
    """Provide the workflow that atomically completes an Intake session."""
    return CompleteIntakeWorkflow(session)


@router.post(
    "",
    response_model=IntakeRead,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
    summary="Legacy one-shot intake",
)
def create_intake(
    data: IntakeCreate,
    service: Annotated[IntakeService, Depends(get_intake_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeRead:
    """Deprecated compatibility API.

    Use `POST /api/intake/sessions` and the IntakeSession item/completion commands instead.
    The session workflow is resumable and creates the Receipt and inventory movements that this
    one-shot endpoint intentionally does not create.
    """
    try:
        return service.create_intake(data, actor_id=_actor_id(current_user))
    except CatalogProductCategoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake category is invalid.",
        ) from exc
    except CatalogVariantProductError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake product is invalid.",
        ) from exc
    except (ImageNotFoundError, ImageLinkEntityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Intake image is invalid.",
        ) from exc
    except ImageLinkPrimaryConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Variant already has a primary image.",
        ) from exc


@router.post("/sessions", response_model=IntakeSessionRead, status_code=status.HTTP_201_CREATED)
def create_intake_session(
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeSessionRead:
    """Start an employee-owned intake workspace without requiring a Supplier."""
    return service.create_session(actor_id=current_user.id)


@router.get("/sessions", response_model=list[IntakeSessionRead])
def list_intake_sessions(
    service: Annotated[IntakeDraftReadService, Depends(get_intake_draft_read_service)],
    current_user: Annotated[User, Depends(get_current_user)],
    session_status: IntakeSessionStatus | None = None,
) -> Sequence[IntakeSessionRead]:
    """Return resumable sessions owned by the authenticated employee."""
    return service.list_sessions(actor_id=current_user.id, status=session_status)


@router.get("/sessions/{session_id}", response_model=IntakeSessionRead)
def get_intake_session(
    session_id: UUIDv7,
    service: Annotated[IntakeDraftReadService, Depends(get_intake_draft_read_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeSessionRead:
    """Return one owned intake workspace with derived missing requirements."""
    try:
        return service.get_session(session_id, actor_id=current_user.id)
    except IntakeSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc


@router.patch("/sessions/{session_id}", response_model=IntakeSessionRead)
def update_intake_session(
    session_id: UUIDv7,
    data: IntakeSessionUpdate,
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeSessionRead:
    """Set late session fields such as Supplier without changing inventory."""
    try:
        return service.update_session(session_id, data, actor_id=current_user.id)
    except IntakeSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc
    except IntakeSessionNotDraftError as exc:
        raise HTTPException(status_code=409, detail="Intake session is not a draft.") from exc
    except IntakeSupplierError as exc:
        raise HTTPException(status_code=400, detail="Intake Supplier is invalid.") from exc


@router.post(
    "/sessions/{session_id}/items/existing",
    response_model=IntakeItemDraftRead,
    status_code=status.HTTP_201_CREATED,
)
def add_existing_intake_item(
    session_id: UUIDv7,
    data: ExistingIntakeItemCreate,
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeItemDraftRead:
    """Add a repeat delivery by Variant ID or exact scanner barcode."""
    try:
        return service.add_existing_item(session_id, data, actor_id=current_user.id)
    except IntakeSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc
    except IntakeSessionNotDraftError as exc:
        raise HTTPException(status_code=409, detail="Intake session is not a draft.") from exc
    except IntakeVariantError as exc:
        raise HTTPException(status_code=400, detail="Intake Variant is invalid.") from exc


@router.post(
    "/sessions/{session_id}/items/new",
    response_model=IntakeItemDraftRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_new_intake_item(
    session_id: UUIDv7,
    file: Annotated[UploadFile, File(...)],
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
    product_id: Annotated[UUIDv7 | None, Form()] = None,
) -> IntakeItemDraftRead:
    """Start a new Product or Variant draft from its mandatory first photo."""
    content = await file.read(ImageService.max_source_size_bytes + 1)
    try:
        return service.add_new_item(
            session_id,
            file.filename or "upload",
            content,
            actor_id=current_user.id,
            product_id=product_id,
        )
    except IntakeSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc
    except IntakeSessionNotDraftError as exc:
        raise HTTPException(status_code=409, detail="Intake session is not a draft.") from exc
    except IntakeProductError as exc:
        raise HTTPException(status_code=400, detail="Intake Product is invalid.") from exc
    except ImageFileTooLargeError as exc:
        raise HTTPException(status_code=413, detail="Image file exceeds the 15 MB limit.") from exc
    except UnsupportedImageError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.patch(
    "/sessions/{session_id}/items/{item_id}",
    response_model=IntakeItemDraftRead,
)
def update_intake_item(
    session_id: UUIDv7,
    item_id: UUIDv7,
    data: IntakeItemDraftUpdate,
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeItemDraftRead:
    """Persist progressive item form data without creating catalog records."""
    try:
        return service.update_item(session_id, item_id, data, actor_id=current_user.id)
    except (IntakeSessionNotFoundError, IntakeItemNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Intake item not found.") from exc
    except (IntakeSessionNotDraftError, IntakeItemNotDraftError) as exc:
        raise HTTPException(status_code=409, detail="Intake item is not mutable.") from exc
    except IntakeCategoryError as exc:
        raise HTTPException(status_code=400, detail="Intake Category is invalid.") from exc
    except IntakeItemFieldError as exc:
        raise HTTPException(status_code=400, detail="Fields do not match item kind.") from exc


@router.post(
    "/sessions/{session_id}/items/{item_id}/abandon",
    response_model=IntakeItemDraftRead,
)
def abandon_intake_item(
    session_id: UUIDv7,
    item_id: UUIDv7,
    data: IntakeAbandon,
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeItemDraftRead:
    """Abandon one item explicitly without deleting its image or history."""
    try:
        return service.abandon_item(
            session_id,
            item_id,
            data.reason,
            actor_id=current_user.id,
        )
    except (IntakeSessionNotFoundError, IntakeItemNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Intake item not found.") from exc
    except (IntakeSessionNotDraftError, IntakeItemNotDraftError) as exc:
        raise HTTPException(status_code=409, detail="Intake item is not mutable.") from exc


@router.post("/sessions/{session_id}/abandon", response_model=IntakeSessionRead)
def abandon_intake_session(
    session_id: UUIDv7,
    data: IntakeAbandon,
    service: Annotated[IntakeDraftWorkflow, Depends(get_intake_draft_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeSessionRead:
    """Abandon one owned session while retaining it for recovery and audit."""
    try:
        return service.abandon_session(
            session_id,
            data.reason,
            actor_id=current_user.id,
        )
    except IntakeSessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc
    except IntakeSessionNotDraftError as exc:
        raise HTTPException(status_code=409, detail="Intake session is not a draft.") from exc


@router.post("/sessions/{session_id}/complete", response_model=IntakeCompletionRead)
def complete_intake_session(
    session_id: UUIDv7,
    service: Annotated[CompleteIntakeWorkflow, Depends(get_complete_intake_workflow)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> IntakeCompletionRead:
    """Atomically materialize catalog facts, post Receipt, and complete the session."""
    try:
        return service.complete(session_id, actor_id=current_user.id)
    except IntakeCompletionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Intake session not found.") from exc
    except IntakeCompletionAbandonedError as exc:
        raise HTTPException(status_code=409, detail="Intake session was abandoned.") from exc
    except IntakeCompletionIncompleteError as exc:
        raise HTTPException(status_code=409, detail="Intake session is incomplete.") from exc

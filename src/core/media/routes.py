from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from core.config import get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.media.inspection import UnsupportedImageError
from core.media.models import Image, ImageLink
from core.media.schemas import (
    ImageCreate,
    ImageLinkCreate,
    ImageLinkRead,
    ImageLinkUpdate,
    ImageRead,
)
from core.media.service import (
    ImageFileTooLargeError,
    ImageLinkEntityError,
    ImageLinkNotFoundError,
    ImageLinkPrimaryConflictError,
    ImageLinkService,
    ImageNotFoundError,
    ImageService,
    ImageStorageKeyError,
)
from core.media.storage import LocalImageStorage
from core.shared.db import UUIDv7

image_router = APIRouter(
    prefix="/api/media/images",
    tags=["media"],
    dependencies=[Depends(get_current_user)],
)
image_link_router = APIRouter(
    prefix="/api/media/image-links",
    tags=["media"],
    dependencies=[Depends(get_current_user)],
)


def _actor_id(current_user: User | None) -> UUIDv7 | None:
    """Return an actor id while allowing existing system-operation test overrides."""
    return current_user.id if current_user is not None else None


def get_image_service(session: Annotated[Session, Depends(get_session)]) -> ImageService:
    """Provide image service instances for route handlers."""
    return ImageService(session, storage=LocalImageStorage(get_settings().storage_root))


def get_image_link_service(session: Annotated[Session, Depends(get_session)]) -> ImageLinkService:
    """Provide image-link service instances for route handlers."""
    return ImageLinkService(session)


@image_router.get("", response_model=list[ImageRead])
def list_images(service: Annotated[ImageService, Depends(get_image_service)]) -> Sequence[Image]:
    """Return all non-deleted image metadata records."""
    return service.list_images()


@image_router.post("", response_model=ImageRead, status_code=status.HTTP_201_CREATED)
def create_image(
    data: ImageCreate,
    service: Annotated[ImageService, Depends(get_image_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Image:
    """Register image metadata without processing or writing a file."""
    try:
        image = service.create_image(data, actor_id=_actor_id(current_user))
        session.commit()
        session.refresh(image)
        return image
    except ImageStorageKeyError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image storage keys must be relative paths.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@image_router.post("/upload", response_model=ImageRead, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: Annotated[UploadFile, File(...)],
    service: Annotated[ImageService, Depends(get_image_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Image:
    """Store one validated local source image and register its metadata."""
    content = await file.read(ImageService.max_source_size_bytes + 1)
    image: Image | None = None
    committed = False
    try:
        image = service.upload_source_image(
            file.filename or "upload", content, actor_id=_actor_id(current_user)
        )
        session.commit()
        committed = True
        session.refresh(image)
        return image
    except ImageFileTooLargeError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Image file exceeds the 15 MB limit.",
        ) from exc
    except UnsupportedImageError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except Exception:
        session.rollback()
        if image is not None and not committed:
            service.discard_uncommitted_source(image)
        raise


@image_router.get("/{image_id}", response_model=ImageRead)
def get_image(
    image_id: UUIDv7,
    service: Annotated[ImageService, Depends(get_image_service)],
) -> Image:
    """Return one image metadata record by id."""
    try:
        return service.get_image(image_id)
    except ImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        ) from exc


@image_router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: UUIDv7,
    service: Annotated[ImageService, Depends(get_image_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Soft-delete image metadata without removing files."""
    try:
        service.delete_image(image_id, actor_id=_actor_id(current_user))
        session.commit()
    except ImageNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found.",
        ) from exc
    except Exception:
        session.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@image_link_router.get("", response_model=list[ImageLinkRead])
def list_image_links(
    service: Annotated[ImageLinkService, Depends(get_image_link_service)],
) -> Sequence[ImageLink]:
    """Return all non-deleted image links."""
    return service.list_links()


@image_link_router.post("", response_model=ImageLinkRead, status_code=status.HTTP_201_CREATED)
def create_image_link(
    data: ImageLinkCreate,
    service: Annotated[ImageLinkService, Depends(get_image_link_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ImageLink:
    """Attach an existing image to an active catalog entity."""
    try:
        link = service.create_link(data, actor_id=_actor_id(current_user))
        session.commit()
        session.refresh(link)
        return link
    except ImageNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image is invalid.",
        ) from exc
    except ImageLinkEntityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image link entity is invalid.",
        ) from exc
    except ImageLinkPrimaryConflictError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity already has a primary image.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@image_link_router.get("/{link_id}", response_model=ImageLinkRead)
def get_image_link(
    link_id: UUIDv7,
    service: Annotated[ImageLinkService, Depends(get_image_link_service)],
) -> ImageLink:
    """Return one image link by id."""
    try:
        return service.get_link(link_id)
    except ImageLinkNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image link not found.",
        ) from exc


@image_link_router.patch("/{link_id}", response_model=ImageLinkRead)
def update_image_link(
    link_id: UUIDv7,
    data: ImageLinkUpdate,
    service: Annotated[ImageLinkService, Depends(get_image_link_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ImageLink:
    """Update the role or display order of an image link."""
    try:
        link = service.update_link(link_id, data, actor_id=_actor_id(current_user))
        session.commit()
        session.refresh(link)
        return link
    except ImageLinkNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image link not found.",
        ) from exc
    except ImageLinkPrimaryConflictError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity already has a primary image.",
        ) from exc
    except Exception:
        session.rollback()
        raise


@image_link_router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image_link(
    link_id: UUIDv7,
    service: Annotated[ImageLinkService, Depends(get_image_link_service)],
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Soft-delete an image link without deleting the linked image."""
    try:
        service.delete_link(link_id, actor_id=_actor_id(current_user))
        session.commit()
    except ImageLinkNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image link not found.",
        ) from exc
    except Exception:
        session.rollback()
        raise
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogProductRepository, CatalogVariantRepository
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.inspection import ImageInspector
from core.media.models import Image, ImageLink
from core.media.repository import ImageLinkRepository, ImageRepository
from core.media.schemas import ImageCreate, ImageLinkCreate, ImageLinkUpdate
from core.media.storage import LocalImageStorage
from core.shared.db import UUIDv7, generate_uuid_v7


class ImageNotFoundError(Exception):
    """Raised when image metadata cannot be found."""


class ImageLinkNotFoundError(Exception):
    """Raised when an image link cannot be found."""


class ImageStorageKeyError(Exception):
    """Raised when image metadata contains a non-relative storage key."""


class ImageLinkEntityError(Exception):
    """Raised when an image link references an unavailable catalog entity."""


class ImageLinkPrimaryConflictError(Exception):
    """Raised when an entity already has an active primary image link."""


class ImageFileTooLargeError(Exception):
    """Raised when uploaded source bytes exceed the local upload limit."""


class ImageService:
    """Business operations for image metadata without physical file handling."""

    max_source_size_bytes = 15 * 1024 * 1024

    def __init__(
        self,
        session: Session,
        storage: LocalImageStorage | None = None,
        inspector: ImageInspector | None = None,
    ) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = ImageRepository(session)
        self._storage = storage
        self._inspector = inspector or ImageInspector()

    def list_images(self) -> Sequence[Image]:
        """Return all non-deleted image metadata records."""
        return self._repository.list()

    def get_image(self, image_id: UUIDv7) -> Image:
        """Return image metadata or raise when it does not exist."""
        image = self._repository.get(image_id)
        if image is None:
            raise ImageNotFoundError
        return image

    def create_image(self, data: ImageCreate, *, actor_id: UUIDv7 | None = None) -> Image:
        """Register immutable-source image metadata without writing any files."""
        self._ensure_relative_keys(data)
        image = Image(**data.model_dump(), created_by_id=actor_id)
        self._repository.add(image)
        self._session.commit()
        self._session.refresh(image)
        return image

    def upload_source_image(
        self,
        original_filename: str,
        content: bytes,
        *,
        commit: bool = True,
        actor_id: UUIDv7 | None = None,
    ) -> Image:
        """Store validated source bytes and create matching immutable image metadata."""
        if len(content) > self.max_source_size_bytes:
            raise ImageFileTooLargeError
        if self._storage is None:
            raise RuntimeError("Local image storage is not configured.")

        inspected = self._inspector.inspect(content)
        image_id = generate_uuid_v7()
        source_key = self._storage.build_source_key(image_id, inspected.extension)
        self._storage.save_source(source_key, content)
        try:
            image = Image(
                id=image_id,
                source_key=source_key,
                original_filename=self._safe_filename(original_filename),
                mime_type=inspected.mime_type,
                size_bytes=len(content),
                width=inspected.width,
                height=inspected.height,
                checksum=sha256(content).hexdigest(),
                created_by_id=actor_id,
            )
            self._repository.add(image)
            if commit:
                self._session.commit()
                self._session.refresh(image)
            else:
                self._session.flush()
            return image
        except Exception:
            if commit:
                self._session.rollback()
            self._storage.delete_saved_source(source_key)
            raise

    def discard_uncommitted_source(self, image: Image) -> None:
        """Remove source bytes after an outer orchestration transaction rolls back."""
        if self._storage is None:
            raise RuntimeError("Local image storage is not configured.")
        self._storage.delete_saved_source(image.source_key)

    def delete_image(self, image_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Soft-delete image metadata without removing physical source files."""
        image = self.get_image(image_id)
        image.soft_delete(actor_id)
        self._session.commit()

    def _ensure_relative_keys(self, data: ImageCreate) -> None:
        """Reject storage keys that could address files outside configured storage."""
        for key in (data.source_key, data.master_key, data.web_key, data.thumb_key):
            if key is not None and not self._is_relative_storage_key(key):
                raise ImageStorageKeyError

    def _is_relative_storage_key(self, key: str) -> bool:
        """Return whether a storage key is a safe non-empty relative POSIX path."""
        path = PurePosixPath(key)
        return bool(key) and not path.is_absolute() and ".." not in path.parts

    def _safe_filename(self, filename: str) -> str:
        """Return a basename suitable for metadata without retaining path components."""
        normalized = filename.replace("\\", "/")
        return normalized.rsplit("/", maxsplit=1)[-1] or "upload"


class ImageLinkService:
    """Business operations for linking images to active catalog entities."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = ImageLinkRepository(session)
        self._image_repository = ImageRepository(session)
        self._product_repository = CatalogProductRepository(session)
        self._variant_repository = CatalogVariantRepository(session)

    def list_links(self) -> Sequence[ImageLink]:
        """Return all non-deleted image links."""
        return self._repository.list()

    def get_link(self, link_id: UUIDv7) -> ImageLink:
        """Return an image link or raise when it does not exist."""
        link = self._repository.get(link_id)
        if link is None:
            raise ImageLinkNotFoundError
        return link

    def create_link(
        self,
        data: ImageLinkCreate,
        *,
        commit: bool = True,
        actor_id: UUIDv7 | None = None,
    ) -> ImageLink:
        """Link an existing image to an active catalog entity."""
        link = self.stage_link(data, actor_id=actor_id)
        if commit:
            self._session.commit()
            self._session.refresh(link)
        return link

    def stage_link(
        self,
        data: ImageLinkCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> ImageLink:
        """Validate and stage an image link inside a caller-owned transaction."""
        self._ensure_image_exists(data.image_id)
        self._ensure_entity_is_active(data.entity_type, data.entity_id)
        self._ensure_primary_available(data.entity_type, data.entity_id, data.role)
        link = ImageLink(**data.model_dump(), created_by_id=actor_id)
        self._repository.add(link)
        self._session.flush()
        return link

    def update_link(
        self,
        link_id: UUIDv7,
        data: ImageLinkUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> ImageLink:
        """Update display fields while preserving image and entity references."""
        link = self.get_link(link_id)
        changes = data.model_dump(exclude_unset=True)
        role = changes.get("role", link.role)
        self._ensure_primary_available(link.entity_type, link.entity_id, role, link.id)
        for field, value in changes.items():
            setattr(link, field, value)
        if actor_id is not None:
            link.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(link)
        return link

    def delete_link(self, link_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Soft-delete an image link without deleting the linked image metadata."""
        link = self.get_link(link_id)
        link.soft_delete(actor_id)
        self._session.commit()

    def _ensure_image_exists(self, image_id: UUIDv7) -> None:
        """Raise when an image is missing or soft-deleted."""
        if self._image_repository.get(image_id) is None:
            raise ImageNotFoundError

    def _ensure_entity_is_active(
        self,
        entity_type: ImageLinkEntityType,
        entity_id: UUIDv7,
    ) -> None:
        """Raise when a target is missing, deleted, or inactive."""
        if entity_type is ImageLinkEntityType.CATALOG_PRODUCT:
            entity = self._product_repository.get(entity_id)
        else:
            entity = self._variant_repository.get(entity_id)
        if entity is None or not entity.is_active:
            raise ImageLinkEntityError

    def _ensure_primary_available(
        self,
        entity_type: ImageLinkEntityType,
        entity_id: UUIDv7,
        role: ImageLinkRole,
        current_link_id: UUIDv7 | None = None,
    ) -> None:
        """Raise when another active primary link already belongs to the entity."""
        if role is not ImageLinkRole.PRIMARY:
            return
        link = self._repository.get_primary_for_entity(entity_type, entity_id)
        if link is not None and link.id != current_link_id:
            raise ImageLinkPrimaryConflictError

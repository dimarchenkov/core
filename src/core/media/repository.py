from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.shared.db import UUIDv7


class ImageRepository:
    """Database access for image metadata."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, image: Image) -> Image:
        """Add image metadata to the current unit of work."""
        self._session.add(image)
        return image

    def get(self, image_id: UUIDv7) -> Image | None:
        """Return a non-deleted image by id."""
        return self._session.scalar(
            select(Image).where(Image.id == image_id, Image.deleted_at.is_(None))
        )

    def list(self) -> Sequence[Image]:
        """Return non-deleted images ordered by latest creation."""
        return self._session.scalars(
            select(Image).where(Image.deleted_at.is_(None)).order_by(Image.created_at.desc())
        ).all()


class ImageLinkRepository:
    """Database access for links between images and catalog entities."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, link: ImageLink) -> ImageLink:
        """Add an image link to the current unit of work."""
        self._session.add(link)
        return link

    def get(self, link_id: UUIDv7) -> ImageLink | None:
        """Return a non-deleted image link by id."""
        return self._session.scalar(
            select(ImageLink).where(ImageLink.id == link_id, ImageLink.deleted_at.is_(None))
        )

    def list(self) -> Sequence[ImageLink]:
        """Return non-deleted image links ordered for display."""
        return self._session.scalars(
            select(ImageLink)
            .where(ImageLink.deleted_at.is_(None))
            .order_by(ImageLink.sort_order, ImageLink.created_at)
        ).all()

    def get_primary_for_entity(
        self,
        entity_type: ImageLinkEntityType,
        entity_id: UUIDv7,
    ) -> ImageLink | None:
        """Return the active primary link for an entity, when one exists."""
        return self._session.scalar(
            select(ImageLink).where(
                ImageLink.entity_type == entity_type,
                ImageLink.entity_id == entity_id,
                ImageLink.role == ImageLinkRole.PRIMARY,
                ImageLink.deleted_at.is_(None),
            )
        )

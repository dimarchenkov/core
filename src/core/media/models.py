from __future__ import annotations

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type[ImageLinkEntityType] | type[ImageLinkRole]) -> list[str]:
    """Return persisted values for media link enums."""
    return [member.value for member in enum_class]


class Image(BaseModel):
    """Immutable-source image metadata stored independently from physical files."""

    __tablename__ = "images"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_images_size_bytes_positive"),
        CheckConstraint("width > 0", name="ck_images_width_positive"),
        CheckConstraint("height > 0", name="ck_images_height_positive"),
    )

    source_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    master_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    web_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    thumb_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(255), nullable=False)
    links: Mapped[list[ImageLink]] = relationship("ImageLink", back_populates="image")


class ImageLink(BaseModel):
    """Role-specific link between an image and a supported catalog entity."""

    __tablename__ = "image_links"

    image_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("images.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    entity_type: Mapped[ImageLinkEntityType] = mapped_column(
        Enum(ImageLinkEntityType, name="image_link_entity_type", values_callable=_enum_values),
        nullable=False,
    )
    entity_id: Mapped[UUIDv7] = mapped_column(nullable=False, index=True)
    role: Mapped[ImageLinkRole] = mapped_column(
        Enum(ImageLinkRole, name="image_link_role", values_callable=_enum_values), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    image: Mapped[Image] = relationship("Image", back_populates="links")

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field

from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.shared.db import UUIDv7


class ImageBase(PydanticBaseModel):
    """Shared image metadata accepted by API schemas."""

    source_key: str = Field(min_length=1, max_length=1024)
    master_key: str | None = Field(default=None, min_length=1, max_length=1024)
    web_key: str | None = Field(default=None, min_length=1, max_length=1024)
    thumb_key: str | None = Field(default=None, min_length=1, max_length=1024)
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    checksum: str = Field(min_length=1, max_length=255)


class ImageCreate(ImageBase):
    """Payload for registering image metadata without file operations."""


class ImageRead(ImageBase):
    """Image metadata representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    created_at: datetime
    updated_at: datetime
    version: int


class ImageLinkBase(PydanticBaseModel):
    """Shared fields for attaching images to catalog entities."""

    image_id: UUIDv7
    entity_type: ImageLinkEntityType
    entity_id: UUIDv7
    role: ImageLinkRole
    sort_order: int = 0


class ImageLinkCreate(ImageLinkBase):
    """Payload for creating an image link."""


class ImageLinkUpdate(PydanticBaseModel):
    """Payload for changing mutable image link display fields."""

    role: ImageLinkRole | None = None
    sort_order: int | None = None


class ImageLinkRead(ImageLinkBase):
    """Image link representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    created_at: datetime
    updated_at: datetime
    version: int

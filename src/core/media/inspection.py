from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image as PillowImage
from PIL import UnidentifiedImageError


class UnsupportedImageError(ValueError):
    """Raised when uploaded content is not a supported valid image."""


@dataclass(frozen=True)
class InspectedImage:
    """Validated image properties derived from the uploaded source bytes."""

    extension: str
    mime_type: str
    width: int
    height: int


class ImageInspector:
    """Validate source image bytes and obtain their format and dimensions."""

    _formats: dict[str, tuple[str, str]] = {
        "JPEG": ("jpg", "image/jpeg"),
        "PNG": ("png", "image/png"),
        "WEBP": ("webp", "image/webp"),
    }

    def inspect(self, content: bytes) -> InspectedImage:
        """Validate uploaded bytes and return properties from their actual image content."""
        try:
            with PillowImage.open(BytesIO(content)) as image:
                image.verify()
            with PillowImage.open(BytesIO(content)) as image:
                image_format = image.format
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise UnsupportedImageError("Uploaded file is not a valid image.") from exc

        if image_format not in self._formats:
            raise UnsupportedImageError("Only JPEG, PNG, and WEBP images are supported.")
        extension, mime_type = self._formats[image_format]
        return InspectedImage(
            extension=extension,
            mime_type=mime_type,
            width=width,
            height=height,
        )

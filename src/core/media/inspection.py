from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image as PillowImage
from PIL import ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener()
logger = logging.getLogger(__name__)

IMAGE_UPLOAD_ERROR = (
    "Не удалось обработать изображение. Выберите фотографию JPEG, PNG, WebP, HEIC или HEIF."
)


class UnsupportedImageError(ValueError):
    """Raised when uploaded content is not a supported valid image."""


@dataclass(frozen=True)
class InspectedImage:
    """Validated image properties derived from the uploaded source bytes."""

    extension: str
    mime_type: str
    width: int
    height: int
    normalized_content: bytes | None = None


class ImageInspector:
    """Validate source image bytes and obtain their format and dimensions."""

    max_pixels = 20_000_000
    _formats: dict[str, tuple[str, str]] = {
        "JPEG": ("jpg", "image/jpeg"),
        "PNG": ("png", "image/png"),
        "WEBP": ("webp", "image/webp"),
    }

    def inspect(self, content: bytes) -> InspectedImage:
        """Validate uploaded bytes and return properties from their actual image content."""
        stage = "open"
        image_format = None
        width = height = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", PillowImage.DecompressionBombWarning)
                with PillowImage.open(BytesIO(content)) as image:
                    image_format = image.format
                    width, height = image.size
                    stage = "dimensions"
                    if image.width * image.height > self.max_pixels:
                        raise UnsupportedImageError(IMAGE_UPLOAD_ERROR)
                    stage = "verify"
                    image.verify()
                stage = "reopen"
                with PillowImage.open(BytesIO(content)) as image:
                    image_format = image.format
                    width, height = image.size
                    stage = "format"
                    if image_format not in {*self._formats, "HEIF", "MPO"}:
                        raise UnsupportedImageError(IMAGE_UPLOAD_ERROR)
                    stage = "decode"
                    image.load()  # Header verification alone cannot detect truncated pixels.
                    if image_format in {"HEIF", "MPO"}:
                        stage = "normalize"
                        # libheif applies HEIF transforms; the plugin resets EXIF orientation.
                        # MPO opens on the primary photo, not an auxiliary frame.
                        # Apply its JPEG EXIF orientation before dropping metadata.
                        upright = ImageOps.exif_transpose(image)
                        output = BytesIO()
                        upright.save(
                            output, format="WEBP", quality=95,
                            icc_profile=image.info.get("icc_profile", b""),
                        )
                        return InspectedImage(
                            "webp", "image/webp", upright.width, upright.height,
                            normalized_content=output.getvalue(),
                        )
        except (
            PillowImage.DecompressionBombError,
            PillowImage.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            ValueError,
        ) as exc:
            # No filename, EXIF, image bytes or raw native exception text in logs.
            logger.warning(
                "Image rejected stage=%s format=%s width=%s height=%s bytes=%s "
                "sha256=%s error=%s",
                stage, image_format, width, height, len(content),
                sha256(content).hexdigest(), type(exc).__name__,
            )
            raise UnsupportedImageError(IMAGE_UPLOAD_ERROR) from exc

        if image_format not in self._formats:
            raise UnsupportedImageError(IMAGE_UPLOAD_ERROR)
        extension, mime_type = self._formats[image_format]
        return InspectedImage(
            extension=extension,
            mime_type=mime_type,
            width=width,
            height=height,
        )

from __future__ import annotations

import os
from io import BytesIO

import pytest
from PIL import Image, ImageCms
from pillow_heif import register_heif_opener

os.environ.setdefault("CORE_ENV", "test")
os.environ.setdefault("CORE_JWT_SECRET", "test-only-jwt-secret-at-least-32-bytes")


@pytest.fixture(scope="session")
def heic_bytes() -> bytes:
    """Encode a real HEVC/HEIF fixture with orientation and an ICC colour profile."""
    register_heif_opener()
    image = Image.new("RGB", (32, 48), "red")
    image.paste("blue", (0, 24, 32, 48))
    image.getexif()[274] = 6
    image.info["icc_profile"] = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    output = BytesIO()
    image.save(output, format="HEIF", quality=95)
    return output.getvalue()


@pytest.fixture(scope="session")
def mpo_bytes() -> bytes:
    """JPEG/MPO primary photo plus a distinct auxiliary frame, like mobile exports."""
    image = Image.new("RGB", (32, 48), "red")
    image.paste("blue", (0, 24, 32, 48))
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    image.save(
        output, format="MPO", save_all=True,
        append_images=[Image.new("RGB", (16, 16), "green")],
        quality=95, exif=exif,
        icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes(),
    )
    return output.getvalue()

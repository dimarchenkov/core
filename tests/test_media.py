from __future__ import annotations

from collections.abc import Generator
from io import BytesIO
from pathlib import Path
from struct import pack
from zlib import crc32

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.media.routes import get_image_service
from core.media.schemas import ImageCreate, ImageLinkCreate
from core.media.service import ImageLinkPrimaryConflictError, ImageLinkService, ImageService
from core.media.storage import LocalImageStorage
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database with media and catalog dependencies."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            Image.__table__,
            ImageLink.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a test client using the in-memory media database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def product(session: Session) -> CatalogProduct:
    """Create an active catalog product for image-link tests."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Film camera", slug="film-camera", category_id=category.id)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def image_payload() -> dict[str, str | int]:
    """Return valid image metadata without touching physical storage."""
    return {
        "source_key": "images/source/camera.jpg",
        "master_key": "images/master/camera.jpg",
        "web_key": "images/web/camera.webp",
        "thumb_key": "images/thumb/camera.webp",
        "original_filename": "camera.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 1_024,
        "width": 800,
        "height": 600,
        "checksum": "sha256:example",
    }


def image_bytes(image_format: str) -> bytes:
    """Create valid in-memory image content for upload endpoint tests."""
    buffer = BytesIO()
    PillowImage.new("RGB", (2, 3), color="red").save(buffer, format=image_format)
    return buffer.getvalue()


def oversized_png_header() -> bytes:
    """Build a PNG header whose dimensions exceed the inspector pixel limit."""
    image_type = b"IHDR"
    image_data = pack(">IIBBBBB", 5_000, 5_000, 8, 2, 0, 0, 0)
    header = pack(">I", len(image_data)) + image_type + image_data
    header += pack(">I", crc32(image_type + image_data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + header + b"\x00\x00\x00\x00IEND\xaeB\x60\x82"


@pytest.fixture
def upload_client(session: Session, tmp_path: Path) -> Generator[TestClient]:
    """Provide a client whose uploads write only to a temporary storage root."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    def override_get_image_service() -> ImageService:
        return ImageService(session, storage=LocalImageStorage(tmp_path))

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_image_service] = override_get_image_service
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_image_service_registers_metadata_with_relative_keys(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image registration persists metadata without any file operations."""
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    monkeypatch.setattr(session, "commit", count_commit)
    image = ImageService(session).create_image(ImageCreate(**image_payload()))

    assert image.source_key == "images/source/camera.jpg"
    assert image.id.version == 7
    assert commit_calls == 0
    assert set(Image.__table__.columns.keys()) == {
        "source_key",
        "master_key",
        "web_key",
        "thumb_key",
        "original_filename",
        "mime_type",
        "size_bytes",
        "width",
        "height",
        "checksum",
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "deleted_by_id",
        "version",
        "created_by_id",
        "updated_by_id",
    }


def test_image_routes_reject_absolute_storage_keys(client: TestClient) -> None:
    """API rejects keys that do not stay relative to configured storage."""
    payload = image_payload()
    payload["source_key"] = "/tmp/camera.jpg"

    response = client.post("/api/media/images", json=payload)

    assert response.status_code == 400


def test_upload_creates_image_metadata_and_preserves_source(
    upload_client: TestClient,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload stores valid JPEG source bytes and returns matching metadata."""
    content = image_bytes("JPEG")
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)

    response = upload_client.post(
        "/api/media/images/upload",
        files={"file": ("camera.jpg", content, "image/jpeg")},
    )

    assert response.status_code == 201
    image = response.json()
    assert image["source_key"].startswith("images/source/")
    assert image["source_key"].endswith(".jpg")
    assert image["mime_type"] == "image/jpeg"
    assert image["width"] == 2
    assert image["height"] == 3
    assert image["size_bytes"] == len(content)
    assert (tmp_path / image["source_key"]).read_bytes() == content
    assert commit_calls == 1


def test_authenticated_source_delivery_preserves_image_content(
    upload_client: TestClient,
) -> None:
    """The workflow client can render uploaded source bytes through the media API."""
    content = image_bytes("JPEG")
    uploaded = upload_client.post(
        "/api/media/images/upload",
        files={"file": ("camera.jpg", content, "image/jpeg")},
    ).json()

    response = upload_client.get(f"/api/media/images/{uploaded['id']}/source")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == content


def test_source_delivery_returns_not_found_for_missing_file(
    client: TestClient,
) -> None:
    """Metadata without available bytes does not leak an invalid filesystem response."""
    image = client.post("/api/media/images", json=image_payload()).json()

    response = client.get(f"/api/media/images/{image['id']}/source")

    assert response.status_code == 404


def test_upload_commit_failure_rolls_back_metadata_and_removes_source(
    upload_client: TestClient,
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The upload command compensates its file when the outer SQL commit fails."""
    original_rollback = session.rollback
    rollback_calls = 0

    def fail_commit() -> None:
        raise RuntimeError("commit failed")

    def count_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    monkeypatch.setattr(session, "commit", fail_commit)
    monkeypatch.setattr(session, "rollback", count_rollback)

    with pytest.raises(RuntimeError, match="commit failed"):
        upload_client.post(
            "/api/media/images/upload",
            files={"file": ("camera.jpg", image_bytes("JPEG"), "image/jpeg")},
        )

    assert rollback_calls == 1
    assert session.query(Image).count() == 0
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_upload_rejects_invalid_image_content(upload_client: TestClient) -> None:
    """Upload rejects arbitrary bytes even when the declared MIME type is JPEG."""
    response = upload_client.post(
        "/api/media/images/upload",
        files={"file": ("not-an-image.jpg", b"not an image", "image/jpeg")},
    )

    assert response.status_code == 415


def test_upload_rejects_unsupported_actual_format(upload_client: TestClient) -> None:
    """Upload rejects valid image content when its detected format is unsupported."""
    response = upload_client.post(
        "/api/media/images/upload",
        files={"file": ("camera.gif", image_bytes("GIF"), "image/jpeg")},
    )

    assert response.status_code == 415


def test_upload_rejects_files_over_maximum_size(upload_client: TestClient) -> None:
    """Upload enforces the 15 MB source-file limit before image inspection."""
    response = upload_client.post(
        "/api/media/images/upload",
        files={
            "file": (
                "large.jpg",
                b"x" * (ImageService.max_source_size_bytes + 1),
                "image/jpeg",
            )
        },
    )

    assert response.status_code == 413


def test_upload_rejects_excessive_image_dimensions(upload_client: TestClient) -> None:
    """Upload rejects image headers that trigger Pillow decompression-bomb protection."""
    response = upload_client.post(
        "/api/media/images/upload",
        files={"file": ("large.png", oversized_png_header(), "image/png")},
    )

    assert response.status_code == 415


def test_failed_storage_write_leaves_no_partial_or_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Atomic storage writes clean temporary files when final replacement fails."""
    storage = LocalImageStorage(tmp_path)
    key = "images/source/2026/07/source.jpg"

    def raise_replace(self: Path, target: Path) -> Path:
        raise OSError("replacement failed")

    monkeypatch.setattr(Path, "replace", raise_replace)

    with pytest.raises(OSError, match="replacement failed"):
        storage.save_source(key, b"source bytes")

    assert not (tmp_path / key).exists()
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_upload_participant_failure_compensates_file_without_rolling_back_owner(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested media operation compensates its file but leaves SQL rollback to its caller."""
    service = ImageService(session, storage=LocalImageStorage(tmp_path))
    original_rollback = session.rollback
    rollback_calls = 0

    def fail_flush() -> None:
        raise RuntimeError("metadata flush failed")

    def count_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    monkeypatch.setattr(session, "flush", fail_flush)
    monkeypatch.setattr(session, "rollback", count_rollback)

    with pytest.raises(RuntimeError, match="metadata flush failed"):
        service.upload_source_image("camera.jpg", image_bytes("JPEG"))

    assert rollback_calls == 0
    assert session.query(Image).count() == 0
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_image_link_service_enforces_one_primary_link(
    session: Session,
    product: CatalogProduct,
) -> None:
    """Only one non-deleted primary image link may belong to an entity."""
    image_service = ImageService(session)
    first_image = image_service.create_image(ImageCreate(**image_payload()))
    second_payload = image_payload()
    second_payload["source_key"] = "images/source/other.jpg"
    second_image = image_service.create_image(ImageCreate(**second_payload))
    service = ImageLinkService(session)
    link_data = ImageLinkCreate(
        image_id=first_image.id,
        entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
        entity_id=product.id,
        role=ImageLinkRole.PRIMARY,
    )
    first_link = service.create_link(link_data)

    with pytest.raises(ImageLinkPrimaryConflictError):
        service.create_link(
            ImageLinkCreate(
                image_id=second_image.id,
                entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
                entity_id=product.id,
                role=ImageLinkRole.PRIMARY,
            )
        )

    service.delete_link(first_link.id)
    replacement = service.create_link(
        ImageLinkCreate(
            image_id=second_image.id,
            entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
            entity_id=product.id,
            role=ImageLinkRole.PRIMARY,
        )
    )

    assert replacement.image_id == second_image.id
    assert ImageService(session).get_image(first_image.id).id == first_image.id


def test_primary_image_lookup_supports_visual_item_confirmation(
    client: TestClient,
    product: CatalogProduct,
) -> None:
    """A client can resolve an entity's primary image without listing all media links."""
    image = client.post("/api/media/images", json=image_payload()).json()
    client.post(
        "/api/media/image-links",
        json={
            "image_id": image["id"],
            "entity_type": "catalog_product",
            "entity_id": str(product.id),
            "role": "primary",
        },
    )

    response = client.get(f"/api/media/image-links/primary/catalog_product/{product.id}")

    assert response.status_code == 200
    assert response.json()["id"] == image["id"]


def test_image_link_routes_require_active_entities_and_images(
    client: TestClient,
    session: Session,
) -> None:
    """Routes reject deleted images and inactive link targets."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    inactive_product = CatalogProduct(
        title="Film camera",
        slug="film-camera",
        category_id=category.id,
        is_active=False,
    )
    session.add(inactive_product)
    session.commit()
    image = client.post("/api/media/images", json=image_payload()).json()

    response = client.post(
        "/api/media/image-links",
        json={
            "image_id": image["id"],
            "entity_type": "catalog_product",
            "entity_id": str(inactive_product.id),
            "role": "gallery",
        },
    )

    assert response.status_code == 400


def test_image_and_link_routes_soft_delete_without_file_cleanup(
    client: TestClient,
    product: CatalogProduct,
) -> None:
    """Deleting a link does not delete its image, while normal lists hide both deletions."""
    image = client.post("/api/media/images", json=image_payload()).json()
    link = client.post(
        "/api/media/image-links",
        json={
            "image_id": image["id"],
            "entity_type": "catalog_product",
            "entity_id": str(product.id),
            "role": "gallery",
        },
    ).json()

    assert client.delete(f"/api/media/image-links/{link['id']}").status_code == 204
    assert client.get(f"/api/media/images/{image['id']}").status_code == 200
    assert client.delete(f"/api/media/images/{image['id']}").status_code == 204
    assert client.get("/api/media/images").json() == []
    assert client.get("/api/media/image-links").json() == []

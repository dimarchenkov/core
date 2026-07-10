from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.media.schemas import ImageCreate, ImageLinkCreate
from core.media.service import ImageLinkPrimaryConflictError, ImageLinkService, ImageService
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


def test_image_service_registers_metadata_with_relative_keys(session: Session) -> None:
    """Image registration persists metadata without any file operations."""
    image = ImageService(session).create_image(ImageCreate(**image_payload()))

    assert image.source_key == "images/source/camera.jpg"
    assert image.id.version == 7
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

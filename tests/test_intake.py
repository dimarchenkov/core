from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.catalog.repository import CatalogProductRepository
from core.database import get_session
from core.intake.schemas import IntakeCreate
from core.intake.service import IntakeService
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.media.schemas import ImageCreate
from core.media.service import ImageNotFoundError, ImageService
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for the intake workflow."""
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
    """Provide a test client using the in-memory intake database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def category_and_image(session: Session) -> tuple[Category, Image]:
    """Create valid category and image dependencies for intake."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.commit()
    image = ImageService(session).create_image(
        ImageCreate(
            source_key="images/source/camera.jpg",
            original_filename="camera.jpg",
            mime_type="image/jpeg",
            size_bytes=1_024,
            width=800,
            height=600,
            checksum="sha256:camera",
        )
    )
    return category, image


def intake_payload(category: Category, image: Image) -> dict[str, str | dict[str, str | bool]]:
    """Return an API payload for a basic catalog intake."""
    return {
        "category_id": str(category.id),
        "product_title": "Film camera",
        "product_description": "35mm camera body",
        "variant_title": "Black body",
        "attributes": {"color": "black", "working": True},
        "image_id": str(image.id),
    }


def test_intake_routes_create_product_variant_and_primary_image_link(
    client: TestClient,
    category_and_image: tuple[Category, Image],
) -> None:
    """One intake creates the required catalog records and generated SKU."""
    category, image = category_and_image

    response = client.post("/api/intake", json=intake_payload(category, image))

    assert response.status_code == 201
    result = response.json()
    assert result["sku"] == "SKU-000001"
    assert result["product_id"]
    assert result["variant_id"]
    assert result["image_link_id"]


def test_intake_service_commits_once(
    session: Session,
    category_and_image: tuple[Category, Image],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intake defers domain-service commits until all records are valid."""
    category, image = category_and_image
    commit_count = 0
    original_commit = session.commit

    def count_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)
    IntakeService(session).create_intake(
        IntakeCreate(
            category_id=category.id,
            product_title="Film camera",
            variant_title="Black body",
            attributes={},
            image_id=image.id,
        )
    )

    assert commit_count == 1


def test_intake_rolls_back_when_image_is_invalid(
    session: Session,
    category_and_image: tuple[Category, Image],
) -> None:
    """Invalid images leave no product or variant persisted by the intake."""
    category, _ = category_and_image

    with pytest.raises(ImageNotFoundError):
        IntakeService(session).create_intake(
            IntakeCreate(
                category_id=category.id,
                product_title="Film camera",
                variant_title="Black body",
                attributes={},
                image_id=generate_uuid_v7(),
            )
        )

    assert CatalogProductRepository(session).list() == []


def test_intake_uses_variant_as_primary_image_target(
    session: Session,
    category_and_image: tuple[Category, Image],
) -> None:
    """The orchestration links the mandatory image as the variant's primary image."""
    category, image = category_and_image
    result = IntakeService(session).create_intake(
        IntakeCreate(
            category_id=category.id,
            product_title="Film camera",
            variant_title="Black body",
            attributes={},
            image_id=image.id,
        )
    )

    image_link = session.get(ImageLink, result.image_link_id)
    assert image_link is not None
    assert image_link.entity_type is ImageLinkEntityType.CATALOG_VARIANT
    assert image_link.entity_id == result.variant_id
    assert image_link.role is ImageLinkRole.PRIMARY

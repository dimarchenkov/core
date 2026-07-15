from __future__ import annotations

from collections.abc import Generator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.identity.models import PrivilegeAuditEvent, User
from core.identity.service import IdentityService
from core.main import create_app
from core.media.models import Image, ImageLink
from core.media.schemas import ImageCreate
from core.media.service import ImageService
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for protected-route tests."""
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
            PrivilegeAuditEvent.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client with the protected routes backed by an in-memory database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_context(session: Session) -> tuple[User, Category, Image]:
    """Create a user and valid catalog/media inputs for authorized requests."""
    user = IdentityService(session).create_admin(
        "admin@example.com",
        "Core Admin",
        "long enough password",
    )
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
    return user, category, image


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in a user and return the bearer header for protected routes."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_business_routes_reject_anonymous_requests(
    client: TestClient,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Catalog, media, and intake endpoints reject requests without a bearer token."""
    _, category, image = authenticated_context
    intake_payload = {
        "category_id": str(category.id),
        "product_title": "Film camera",
        "variant_title": "Black body",
        "attributes": {},
        "image_id": str(image.id),
    }

    assert client.get("/api/catalog/categories").status_code == 401
    assert client.get("/api/media/images").status_code == 401
    assert client.post("/api/intake", json=intake_payload).status_code == 401


def test_invalid_bearer_token_is_rejected_by_business_routes(client: TestClient) -> None:
    """Business routes use the shared dependency to reject invalid bearer tokens."""
    response = client.get(
        "/api/catalog/categories",
        headers={"Authorization": "Bearer not-a-jwt"},
    )

    assert response.status_code == 401


def test_valid_bearer_token_permits_protected_areas(
    client: TestClient,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Any active authenticated user can access catalog, media, and intake routes."""
    user, category, image = authenticated_context
    headers = authorization_header(client, user)

    catalog_response = client.get("/api/catalog/categories", headers=headers)
    media_response = client.get("/api/media/images", headers=headers)
    intake_response = client.post(
        "/api/intake",
        headers=headers,
        json={
            "category_id": str(category.id),
            "product_title": "Film camera",
            "variant_title": "Black body",
            "attributes": {"color": "black"},
            "image_id": str(image.id),
        },
    )

    assert catalog_response.status_code == 200
    assert media_response.status_code == 200
    assert intake_response.status_code == 201


def test_health_and_login_remain_public(
    client: TestClient,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Health and login endpoints remain available without bearer authentication."""
    user, _, _ = authenticated_context

    health_response = client.get("/health")
    login_response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )

    assert health_response.status_code == 200
    assert login_response.status_code == 200


def test_authenticated_catalog_validation_is_preserved(
    client: TestClient,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Authentication does not replace existing catalog conflict validation."""
    user, category, _ = authenticated_context
    headers = authorization_header(client, user)

    response = client.post(
        "/api/catalog/categories",
        headers=headers,
        json={"title": "Other cameras", "slug": category.slug},
    )

    assert response.status_code == 409


def test_authenticated_catalog_writes_record_attribution(
    client: TestClient,
    session: Session,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Authenticated create, update, and soft delete record the acting user."""
    user, _, _ = authenticated_context
    headers = authorization_header(client, user)

    created = client.post(
        "/api/catalog/categories",
        headers=headers,
        json={"title": "Lenses", "slug": "lenses"},
    )
    category = session.get(Category, UUID(created.json()["id"]))
    assert created.status_code == 201
    assert category is not None
    assert category.created_by_id == user.id
    assert category.updated_by_id is None
    assert category.deleted_by_id is None

    read = client.get(f"/api/catalog/categories/{category.id}", headers=headers)
    session.refresh(category)
    assert read.status_code == 200
    assert category.updated_by_id is None

    updated = client.patch(
        f"/api/catalog/categories/{category.id}",
        headers=headers,
        json={"title": "Prime Lenses"},
    )
    session.refresh(category)
    assert updated.status_code == 200
    assert category.updated_by_id == user.id

    deleted = client.delete(f"/api/catalog/categories/{category.id}", headers=headers)
    session.refresh(category)
    assert deleted.status_code == 204
    assert category.deleted_by_id == user.id


def test_authenticated_media_writes_record_attribution(
    client: TestClient,
    session: Session,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Authenticated image creation and deletion record the acting user."""
    user, _, _ = authenticated_context
    headers = authorization_header(client, user)
    response = client.post(
        "/api/media/images",
        headers=headers,
        json={
            "source_key": "images/source/attributed.jpg",
            "original_filename": "attributed.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 1_024,
            "width": 800,
            "height": 600,
            "checksum": "sha256:attributed",
        },
    )
    image = session.get(Image, UUID(response.json()["id"]))
    assert response.status_code == 201
    assert image is not None
    assert image.created_by_id == user.id

    deleted = client.delete(f"/api/media/images/{image.id}", headers=headers)
    session.refresh(image)
    assert deleted.status_code == 204
    assert image.deleted_by_id == user.id


def test_authenticated_intake_records_one_actor_and_rolls_back_on_failure(
    client: TestClient,
    session: Session,
    authenticated_context: tuple[User, Category, Image],
) -> None:
    """Intake attributes all created records and keeps failed work atomic."""
    user, category, image = authenticated_context
    headers = authorization_header(client, user)
    response = client.post(
        "/api/intake",
        headers=headers,
        json={
            "category_id": str(category.id),
            "product_title": "Film camera",
            "variant_title": "Black body",
            "attributes": {"color": "black"},
            "image_id": str(image.id),
        },
    )
    assert response.status_code == 201
    result = response.json()
    product = session.get(CatalogProduct, UUID(result["product_id"]))
    variant = session.get(CatalogVariant, UUID(result["variant_id"]))
    image_link = session.get(ImageLink, UUID(result["image_link_id"]))
    assert product is not None and product.created_by_id == user.id
    assert variant is not None and variant.created_by_id == user.id
    assert image_link is not None and image_link.created_by_id == user.id

    failed = client.post(
        "/api/intake",
        headers=headers,
        json={
            "category_id": str(category.id),
            "product_title": "Unlinked camera",
            "variant_title": "Body",
            "attributes": {},
            "image_id": "019f64e4-a309-742b-b1ca-b6059d31bce5",
        },
    )
    assert failed.status_code == 400
    assert session.query(CatalogProduct).count() == 1
    assert session.query(CatalogVariant).count() == 1
    assert session.query(ImageLink).count() == 1
    session.refresh(image)
    assert image.deleted_at is None


def test_base_model_audit_foreign_keys_reference_users_with_set_null() -> None:
    """All shared audit columns have nullable SET NULL user foreign keys."""
    for table in (
        Category.__table__,
        CatalogProduct.__table__,
        CatalogVariant.__table__,
        Image.__table__,
        ImageLink.__table__,
        User.__table__,
    ):
        for column_name in ("created_by_id", "updated_by_id", "deleted_by_id"):
            column = table.c[column_name]
            foreign_key = next(iter(column.foreign_keys))
            assert column.nullable is True
            assert foreign_key.target_fullname == "users.id"
            assert foreign_key.ondelete == "SET NULL"

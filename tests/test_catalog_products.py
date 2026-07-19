from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, Category
from core.catalog.schemas import CatalogProductCreate
from core.catalog.service import CatalogProductService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database with catalog product dependencies."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Category.__table__, CatalogProduct.__table__],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a test client using the in-memory catalog database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def active_category(session: Session) -> Category:
    """Create an active category suitable for product assignment."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


def test_product_service_creates_product(
    session: Session,
    active_category: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Products can be created with only catalog-family fields."""
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    monkeypatch.setattr(session, "commit", count_commit)
    product = CatalogProductService(session).create_product(
        CatalogProductCreate(
            title="35mm Camera",
            slug="35mm-camera",
            description="Film camera body",
            category_id=active_category.id,
        ),
    )

    assert product.id.version == 7
    assert product.description == "Film camera body"
    assert product.is_active is True
    assert commit_calls == 0
    assert set(CatalogProduct.__table__.columns.keys()) == {
        "title",
        "slug",
        "description",
        "category_id",
        "is_active",
        "id",
        "created_at",
        "updated_at",
        "deleted_at",
        "deleted_by_id",
        "version",
        "created_by_id",
        "updated_by_id",
    }


def test_product_route_owns_one_commit(
    client: TestClient,
    session: Session,
    active_category: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP command, not CatalogProductService, finalizes product creation."""
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)

    response = client.post(
        "/api/catalog/products",
        json={
            "title": "Camera",
            "slug": "camera",
            "category_id": str(active_category.id),
        },
    )

    assert response.status_code == 201
    assert commit_calls == 1


def test_product_routes_reject_inactive_category(client: TestClient, session: Session) -> None:
    """Products cannot be assigned to inactive categories."""
    category = Category(title="Inactive", slug="inactive", is_active=False)
    session.add(category)
    session.commit()

    response = client.post(
        "/api/catalog/products",
        json={"title": "Camera", "slug": "camera", "category_id": str(category.id)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Product category is invalid."


def test_product_routes_reject_duplicate_active_slug(
    client: TestClient,
    active_category: Category,
) -> None:
    """Only one non-deleted product can use a catalog slug."""
    payload = {"title": "Camera", "slug": "camera", "category_id": str(active_category.id)}

    assert client.post("/api/catalog/products", json=payload).status_code == 201
    response = client.post(
        "/api/catalog/products",
        json={**payload, "title": "Other camera"},
    )

    assert response.status_code == 409


def test_product_routes_soft_delete_and_reuse_slug(
    client: TestClient,
    active_category: Category,
) -> None:
    """Soft-deleted products disappear from normal views and release their slug."""
    payload = {"title": "Camera", "slug": "camera", "category_id": str(active_category.id)}
    created = client.post("/api/catalog/products", json=payload).json()

    assert client.delete(f"/api/catalog/products/{created['id']}").status_code == 204
    assert client.get(f"/api/catalog/products/{created['id']}").status_code == 404
    assert client.get("/api/catalog/products").json() == []
    assert client.post("/api/catalog/products", json=payload).status_code == 201


def test_product_routes_update_fields(
    client: TestClient,
    active_category: Category,
) -> None:
    """Product routes update supported product-family fields."""
    created = client.post(
        "/api/catalog/products",
        json={"title": "Camera", "slug": "camera", "category_id": str(active_category.id)},
    ).json()

    response = client.patch(
        f"/api/catalog/products/{created['id']}",
        json={"description": "Updated", "is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["description"] == "Updated"
    assert response.json()["is_active"] is False


def test_product_routes_require_title_and_existing_category(client: TestClient) -> None:
    """Product API validates required titles and category references."""
    missing_title = client.post("/api/catalog/products", json={"slug": "camera"})
    missing_category = client.post(
        "/api/catalog/products",
        json={
            "title": "Camera",
            "slug": "camera",
            "category_id": "0189c98a-1d24-7f8f-b2f0-6f40fd23f860",
        },
    )

    assert missing_title.status_code == 422
    assert missing_category.status_code == 400

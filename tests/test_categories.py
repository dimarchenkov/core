from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import Category
from core.catalog.schemas import CategoryCreate, CategoryUpdate
from core.catalog.service import CategoryService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Category.__table__])
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: None

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_category_service_creates_category(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CategoryService(session)
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    monkeypatch.setattr(session, "commit", count_commit)

    category = service.create_category(
        CategoryCreate(title="Cameras", slug="cameras", sort_order=10),
    )

    assert category.id.version == 7
    assert category.title == "Cameras"
    assert category.slug == "cameras"
    assert category.parent_id is None
    assert category.sort_order == 10
    assert category.is_active is True
    assert category.created_by_id is None
    assert commit_calls == 0


def test_category_service_creates_child_category(session: Session) -> None:
    service = CategoryService(session)
    parent = service.create_category(CategoryCreate(title="Photo", slug="photo"))

    child = service.create_category(
        CategoryCreate(title="Lenses", slug="lenses", parent_id=parent.id),
    )

    assert child.parent_id == parent.id


def test_category_routes_create_and_list_categories(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)
    create_response = client.post(
        "/api/catalog/categories",
        json={"title": "Cameras", "slug": "cameras", "sort_order": 10},
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["title"] == "Cameras"
    assert created["slug"] == "cameras"
    assert created["sort_order"] == 10

    list_response = client.get("/api/catalog/categories")

    assert list_response.status_code == 200
    assert [category["slug"] for category in list_response.json()] == ["cameras"]
    assert commit_calls == 1


def test_category_routes_update_category(client: TestClient) -> None:
    created = client.post(
        "/api/catalog/categories",
        json={"title": "Cameras", "slug": "cameras"},
    ).json()

    response = client.patch(
        f"/api/catalog/categories/{created['id']}",
        json={"title": "Digital Cameras", "is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Digital Cameras"
    assert response.json()["is_active"] is False


def test_category_routes_get_category(client: TestClient) -> None:
    """Category routes return an existing category by its identifier."""
    created = client.post(
        "/api/catalog/categories",
        json={"title": "Cameras", "slug": "cameras"},
    ).json()

    response = client.get(f"/api/catalog/categories/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["slug"] == "cameras"


def test_category_routes_reject_duplicate_slug(client: TestClient) -> None:
    first_response = client.post(
        "/api/catalog/categories",
        json={"title": "Cameras", "slug": "cameras"},
    )
    second_response = client.post(
        "/api/catalog/categories",
        json={"title": "Other Cameras", "slug": "cameras"},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


def test_category_routes_soft_delete_category(client: TestClient) -> None:
    created = client.post(
        "/api/catalog/categories",
        json={"title": "Cameras", "slug": "cameras"},
    ).json()

    delete_response = client.delete(f"/api/catalog/categories/{created['id']}")
    get_response = client.get(f"/api/catalog/categories/{created['id']}")
    list_response = client.get("/api/catalog/categories")

    assert delete_response.status_code == 204
    assert get_response.status_code == 404
    assert list_response.json() == []


def test_category_service_updates_category(session: Session) -> None:
    service = CategoryService(session)
    category = service.create_category(CategoryCreate(title="Photo", slug="photo"))

    updated = service.update_category(
        category.id,
        CategoryUpdate(title="Photography", slug="photography", sort_order=5),
    )

    assert updated.title == "Photography"
    assert updated.slug == "photography"
    assert updated.sort_order == 5

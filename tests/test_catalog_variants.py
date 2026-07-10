from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.catalog.schemas import CatalogVariantCreate, CatalogVariantUpdate
from core.catalog.service import CatalogVariantProductError, CatalogVariantService
from core.catalog.sku import SkuGenerator
from core.database import get_session
from core.main import create_app
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database with catalog variant dependencies."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[Category.__table__, CatalogProduct.__table__, CatalogVariant.__table__],
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def active_product(session: Session) -> CatalogProduct:
    """Create an active product suitable for variant assignment."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Film camera", slug="film-camera", category_id=category.id)
    session.add(product)
    session.commit()
    session.refresh(product)
    return product


def test_variant_service_generates_stable_sequential_skus(
    session: Session,
    active_product: CatalogProduct,
) -> None:
    """The service creates the next stable SKU without user input."""
    service = CatalogVariantService(session)
    first = service.create_variant(
        CatalogVariantCreate(product_id=active_product.id, title="Camera body"),
    )
    second = service.create_variant(
        CatalogVariantCreate(product_id=active_product.id, title="Camera body kit"),
    )

    assert first.sku == "SKU-000001"
    assert second.sku == "SKU-000002"
    assert set(CatalogVariant.__table__.columns.keys()) == {
        "product_id",
        "title",
        "sku",
        "barcode",
        "attributes",
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


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "SKU-000001"), (42, "SKU-000042"), (1_000_000, "SKU-1000000")],
)
def test_sku_generator_formats_reserved_numbers(number: int, expected: str) -> None:
    """SKU formatting is isolated from variant persistence and services."""
    assert SkuGenerator.generate(number) == expected


@pytest.mark.parametrize("number", [0, -1])
def test_sku_generator_rejects_non_positive_numbers(number: int) -> None:
    """Only positive sequence numbers may be converted into SKUs."""
    with pytest.raises(ValueError, match="positive"):
        SkuGenerator.generate(number)


def test_variant_service_rejects_inactive_product(session: Session) -> None:
    """A variant cannot be assigned to an inactive product."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    product = CatalogProduct(
        title="Film camera",
        slug="film-camera",
        category_id=category.id,
        is_active=False,
    )
    session.add(product)
    session.commit()

    with pytest.raises(CatalogVariantProductError):
        CatalogVariantService(session).create_variant(
            CatalogVariantCreate(product_id=product.id, title="Camera body"),
        )


def test_variant_routes_reject_user_supplied_sku(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """The API rejects SKU input because Core generates SKUs itself."""
    response = client.post(
        "/api/catalog/variants",
        json={
            "product_id": str(active_product.id),
            "title": "Camera body",
            "sku": "MANUAL-1",
        },
    )

    assert response.status_code == 422


def test_variant_routes_update_without_changing_sku(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """Updates retain the system-generated SKU and reject SKU changes."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()
    update = client.patch(
        f"/api/catalog/variants/{created['id']}",
        json={"title": "Updated camera body", "barcode": "123456"},
    )
    sku_change = client.patch(
        f"/api/catalog/variants/{created['id']}",
        json={"sku": "MANUAL-1"},
    )

    assert update.status_code == 200
    assert update.json()["sku"] == "SKU-000001"
    assert update.json()["barcode"] == "123456"
    assert sku_change.status_code == 422


def test_variant_routes_soft_delete(client: TestClient, active_product: CatalogProduct) -> None:
    """Deleting a variant hides it from normal variant endpoints."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()

    assert client.delete(f"/api/catalog/variants/{created['id']}").status_code == 204
    assert client.get(f"/api/catalog/variants/{created['id']}").status_code == 404
    assert client.get("/api/catalog/variants").json() == []


def test_variant_update_schema_has_no_sku_field() -> None:
    """The update schema prevents any changes to stable generated SKUs."""
    assert "sku" not in CatalogVariantUpdate.model_fields

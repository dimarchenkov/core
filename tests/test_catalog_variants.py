from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcode import InternalBarcodeGenerator
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.catalog.schemas import CatalogVariantCreate, CatalogVariantUpdate
from core.catalog.service import CatalogVariantProductError, CatalogVariantService
from core.catalog.sku import SkuGenerator
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
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
        tables=[
            User.__table__,
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
        ],
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service creates the next stable SKU without user input."""
    service = CatalogVariantService(session)
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    monkeypatch.setattr(session, "commit", count_commit)
    first = service.create_variant(
        CatalogVariantCreate(product_id=active_product.id, title="Camera body"),
    )
    second = service.create_variant(
        CatalogVariantCreate(product_id=active_product.id, title="Camera body kit"),
    )

    assert first.sku == "SKU-000001"
    assert second.sku == "SKU-000002"
    assert first.barcode == "2000000000015"
    assert second.barcode == "2000000000022"
    assert commit_calls == 0
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


def test_variant_route_owns_one_commit(
    client: TestClient,
    session: Session,
    active_product: CatalogProduct,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP command, not CatalogVariantService, finalizes variant creation."""
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)

    response = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    )

    assert response.status_code == 201
    assert commit_calls == 1


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


@pytest.mark.parametrize(
    ("number", "expected"),
    [(1, "2000000000015"), (42, "2000000000428"), (1_000_000, "2000010000005")],
)
def test_internal_barcode_generator_creates_valid_ean13(number: int, expected: str) -> None:
    """Internal variant numbers use the restricted 20 prefix and EAN check digit."""
    barcode = InternalBarcodeGenerator.generate(number)

    assert barcode == expected
    assert len(barcode) == 13
    assert barcode.isdigit()
    assert barcode.startswith("20")


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


def test_variant_routes_update_without_changing_identifiers(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """Updates retain system-generated identifiers and reject their changes."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()
    update = client.patch(
        f"/api/catalog/variants/{created['id']}",
        json={"title": "Updated camera body"},
    )
    sku_change = client.patch(
        f"/api/catalog/variants/{created['id']}",
        json={"sku": "MANUAL-1"},
    )
    barcode_change = client.patch(
        f"/api/catalog/variants/{created['id']}",
        json={"barcode": "123456"},
    )

    assert update.status_code == 200
    assert update.json()["sku"] == "SKU-000001"
    assert update.json()["barcode"] == "2000000000015"
    assert sku_change.status_code == 422
    assert barcode_change.status_code == 422


def test_variant_routes_soft_delete(client: TestClient, active_product: CatalogProduct) -> None:
    """Deleting a variant hides it from normal variant endpoints."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()

    assert client.delete(f"/api/catalog/variants/{created['id']}").status_code == 204
    assert client.get(f"/api/catalog/variants/{created['id']}").status_code == 404
    assert client.get("/api/catalog/variants").json() == []


def test_variant_can_be_found_by_exact_barcode_and_archived_code_is_hidden(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """Scanner lookup resolves one exact active record and hides archived variants."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()
    path = f"/api/catalog/variants/by-barcode/{created['barcode']}"

    found = client.get(path)

    assert found.status_code == 200
    assert found.json()["id"] == created["id"]
    assert client.get("/api/catalog/variants/by-barcode/9999999999999").status_code == 404

    assert client.delete(f"/api/catalog/variants/{created['id']}").status_code == 204
    assert client.get(path).status_code == 404


def test_variant_update_schema_has_no_sku_field() -> None:
    """The update schema prevents any changes to stable generated SKUs."""
    assert "sku" not in CatalogVariantUpdate.model_fields
    assert "barcode" not in CatalogVariantUpdate.model_fields

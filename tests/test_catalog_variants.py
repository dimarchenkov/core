from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcode import InternalBarcodeGenerator
from core.catalog.barcodes import (
    BarcodeFormat,
    BarcodeSource,
    BarcodeValidationError,
    detect_barcode_format,
    normalize_barcode,
)
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
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
            CatalogVariantBarcode.__table__,
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
        "barcode_source",
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
    assert client.get("/api/catalog/variants/by-barcode/9999999999999").status_code == 422

    assert client.delete(f"/api/catalog/variants/{created['id']}").status_code == 204
    assert client.get(path).status_code == 404


def test_variant_update_schema_has_no_sku_field() -> None:
    """The update schema prevents any changes to stable generated SKUs."""
    assert "sku" not in CatalogVariantUpdate.model_fields
    assert "barcode" not in CatalogVariantUpdate.model_fields


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("4601234567893", BarcodeFormat.EAN_13),
        ("12345670", BarcodeFormat.EAN_8),
        ("036000291452", BarcodeFormat.UPC_A),
        ("LOT-A/42", BarcodeFormat.CODE_128),
    ],
)
def test_supported_manufacturer_barcode_formats(value: str, expected: BarcodeFormat) -> None:
    """The shared validator accepts production scanner formats and detects them."""
    assert normalize_barcode(f"  {value}\n") == value
    assert detect_barcode_format(value) == expected


def test_gtin_with_invalid_check_digit_is_rejected() -> None:
    """A numeric GTIN-shaped value cannot silently fall back to Code 128."""
    with pytest.raises(BarcodeValidationError, match="check digit"):
        normalize_barcode("4601234567894")


def test_variant_has_one_globally_unique_operational_barcode(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """An external code is the sole operational identity and cannot be reassigned."""
    first = client.post(
        "/api/catalog/variants",
        json={
            "product_id": str(active_product.id),
            "title": "Camera body",
            "manufacturer_barcode": "4601234567893",
        },
    )
    second = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera kit"},
    )

    assert first.status_code == 201
    assert first.json()["barcode"] == "4601234567893"
    assert first.json()["barcode_source"] == BarcodeSource.MANUFACTURER
    lookup = client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "4601234567893"},
    )
    assert lookup.status_code == 200
    assert lookup.json()["id"] == first.json()["id"]
    conflict = client.post(
        f"/api/catalog/variants/{second.json()['id']}/barcodes",
        json={"value": "4601234567893", "source": "manufacturer"},
    )
    assert conflict.status_code == 409
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "2000000000015"},
    ).status_code == 404


def test_replacing_barcode_retires_old_scanner_identity(
    client: TestClient,
    session: Session,
    active_product: CatalogProduct,
) -> None:
    """Replacement installs one external code and keeps the old row only as history."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()
    old_barcode = created["barcode"]

    replaced = client.put(
        f"/api/catalog/variants/{created['id']}/barcode",
        json={"value": "4601234567893"},
    )

    assert replaced.status_code == 200
    assert replaced.json()["barcode"] == "4601234567893"
    assert replaced.json()["barcode_source"] == BarcodeSource.MANUFACTURER
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode", params={"barcode": old_barcode}
    ).status_code == 404
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "4601234567893"},
    ).status_code == 200

    replaced_again = client.put(
        f"/api/catalog/variants/{created['id']}/barcode",
        json={"value": "036000291452"},
    )
    assert replaced_again.status_code == 200
    assert replaced_again.json()["barcode"] == "036000291452"
    assert replaced_again.json()["barcode_source"] == BarcodeSource.MANUFACTURER
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "4601234567893"},
    ).status_code == 404
    history = session.query(CatalogVariantBarcode).order_by(
        CatalogVariantBarcode.created_at
    ).all()
    assert [(row.value, row.deleted_at is None) for row in history] == [
        (old_barcode, False),
        ("4601234567893", False),
        ("036000291452", True),
    ]


def test_deleting_external_barcode_generates_fresh_internal_identity(
    client: TestClient,
    session: Session,
    active_product: CatalogProduct,
) -> None:
    """External deletion preserves history and never revives the reserved internal code."""
    created = client.post(
        "/api/catalog/variants",
        json={
            "product_id": str(active_product.id),
            "title": "Camera body",
            "manufacturer_barcode": "4601234567893",
        },
    ).json()

    deleted = client.delete(f"/api/catalog/variants/{created['id']}/barcode")

    assert deleted.status_code == 200
    assert deleted.json()["barcode_source"] == BarcodeSource.INTERNAL
    assert deleted.json()["barcode"].startswith("20")
    assert deleted.json()["barcode"] != "2000000000015"
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "4601234567893"},
    ).status_code == 404
    history = session.query(CatalogVariantBarcode).all()
    assert sum(row.deleted_at is None for row in history) == 1
    assert any(row.value == "4601234567893" and row.deleted_at is not None for row in history)


def test_system_barcode_cannot_be_deleted(
    client: TestClient,
    active_product: CatalogProduct,
) -> None:
    """The backend protects the only system-generated operational identity."""
    created = client.post(
        "/api/catalog/variants",
        json={"product_id": str(active_product.id), "title": "Camera body"},
    ).json()

    response = client.delete(f"/api/catalog/variants/{created['id']}/barcode")

    assert response.status_code == 409
    assert client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": created["barcode"]},
    ).status_code == 200

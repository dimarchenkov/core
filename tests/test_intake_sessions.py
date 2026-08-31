from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.activity.enums import ActivityEventType
from core.activity.models import ActivityEvent
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.intake.completion import CompleteIntakeWorkflow
from core.intake.draft_service import IntakeDraftWorkflow
from core.intake.models import IntakeItemDraft, IntakeSession
from core.intake.routes import get_intake_draft_workflow
from core.inventory.models import StockMovement
from core.main import create_app
from core.media.enums import ImageLinkRole
from core.media.models import Image, ImageLink
from core.media.service import ImageService
from core.media.storage import LocalImageStorage
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.receipt.models import Receipt, ReceiptItem
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord
from core.rental.repository import RentalAssetRepository
from core.shared.db import Base
from core.supplier.models import Supplier


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the tables used by resumable intake orchestration."""
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
            Image.__table__,
            ImageLink.__table__,
            Supplier.__table__,
            Receipt.__table__,
            ReceiptItem.__table__,
            StockMovement.__table__,
            Price.__table__,
            IntakeSession.__table__,
            IntakeItemDraft.__table__,
            RentalAssetRecord.__table__,
            ActivityEvent.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def users(session: Session) -> tuple[User, User]:
    """Create two employees for ownership and attribution checks."""
    first = User(
        email="first@example.com",
        full_name="First Employee",
        password_hash="unused",
    )
    second = User(
        email="second@example.com",
        full_name="Second Employee",
        password_hash="unused",
    )
    session.add_all([first, second])
    session.commit()
    return first, second


@pytest.fixture
def client(
    session: Session,
    users: tuple[User, User],
    tmp_path: Path,
) -> Generator[tuple[TestClient, User, User, Path]]:
    """Provide an authenticated app with isolated image storage."""
    first, second = users
    storage_root = tmp_path / "storage"
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: first
    app.dependency_overrides[get_intake_draft_workflow] = lambda: IntakeDraftWorkflow(
        session,
        ImageService(session, storage=LocalImageStorage(storage_root)),
    )
    with TestClient(app) as test_client:
        yield test_client, first, second, storage_root
    app.dependency_overrides.clear()


@pytest.fixture
def catalog(session: Session) -> tuple[Category, CatalogProduct, CatalogVariant]:
    """Create one scannable existing Variant and its active catalog parents."""
    category = Category(title="Storage", slug="storage")
    session.add(category)
    session.flush()
    product = CatalogProduct(
        title="Shoe rack",
        slug="shoe-rack",
        category_id=category.id,
    )
    session.add(product)
    session.flush()
    variant = CatalogVariant(
        product_id=product.id,
        title="Grey",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={"color": "grey"},
    )
    session.add(variant)
    session.commit()
    return category, product, variant


@pytest.fixture
def supplier(session: Session) -> Supplier:
    """Create an active Supplier selected late in the workflow."""
    value = Supplier(name="Test Supplier", display_name="Supplier", code="SUP-001")
    session.add(value)
    session.commit()
    return value


def _png_bytes() -> bytes:
    """Return a small valid PNG suitable for multipart upload tests."""
    buffer = BytesIO()
    PillowImage.new("RGB", (8, 6), color="blue").save(buffer, format="PNG")
    return buffer.getvalue()


def _create_session(client: TestClient) -> dict[str, object]:
    """Start and return one owned intake session through the public API."""
    response = client.post("/api/intake/sessions")
    assert response.status_code == 201
    return response.json()


def test_product_autosave_partial_patches_survive_reload(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
) -> None:
    """Independent Product autosaves persist without changing Variant draft data."""
    test_client, _, _, _ = client
    intake = _create_session(test_client)
    base = f"/api/intake/sessions/{intake['id']}"
    created = test_client.post(
        f"{base}/items/new", files={"file": ("photo.png", _png_bytes(), "image/png")}
    ).json()
    endpoint = f"{base}/items/{created['id']}"
    values = {
        "category_id": str(catalog[0].id),
        "product_title": "Autosaved product",
        "product_description": "Autosaved description",
    }
    for key, value in values.items():
        assert test_client.patch(endpoint, json={key: value}).status_code == 200
    resumed = test_client.get(base).json()["items"][0]
    for key, value in values.items():
        assert resumed[key] == value
    assert resumed["reserved_sku"] == created["reserved_sku"]
    assert resumed["reserved_internal_barcode"] == created["reserved_internal_barcode"]
    assert resumed["quantity"] == created["quantity"]


def test_heic_intake_product_and_variant_share_ingestion(
    client: tuple[TestClient, User, User, Path], heic_bytes: bytes, session: Session,
) -> None:
    test_client, _, _, storage_root = client
    intake = _create_session(test_client)
    base = f"/api/intake/sessions/{intake['id']}/items"
    root = test_client.post(
        f"{base}/new", files={"file": ("photo.heic", heic_bytes, "image/heic")}
    )
    assert root.status_code == 201
    child = test_client.post(
        f"{base}/new", data={"draft_product_item_id": root.json()["id"]},
        files={"file": ("photo.heif", heic_bytes, "image/heif")},
    )
    assert child.status_code == 201
    for item in [root.json(), child.json()]:
        image = session.get(Image, UUID(item["image_id"]))
        assert image.mime_type == "image/webp"
        with PillowImage.open(storage_root / image.source_key) as decoded:
            assert decoded.size == (48, 32)
        replaced = test_client.put(
            f"{base}/{item['id']}/image",
            files={"file": ("replacement.heic", heic_bytes, "application/octet-stream")},
        )
        assert replaced.status_code == 200
        assert replaced.json()["image_id"] != item["image_id"]


def test_session_starts_before_supplier_and_is_resumable(
    client: tuple[TestClient, User, User, Path],
    session: Session,
) -> None:
    """An employee can start empty work and retrieve it later without Supplier data."""
    test_client, first, _, _ = client

    created = _create_session(test_client)
    resumed = test_client.get(f"/api/intake/sessions/{created['id']}")

    assert resumed.status_code == 200
    assert resumed.json()["owner_id"] == str(first.id)
    assert resumed.json()["status"] == "draft"
    assert resumed.json()["missing_requirements"] == [
        "missing_supplier",
        "missing_items",
    ]
    events = session.scalars(select(ActivityEvent)).all()
    assert [event.event_type for event in events] == [ActivityEventType.INTAKE_SESSION_STARTED]
    assert events[0].actor_id == first.id
    assert events[0].entity_id == UUID(created["id"])


def test_employee_cannot_see_another_employees_session(
    client: tuple[TestClient, User, User, Path],
) -> None:
    """Ownership is enforced without leaking that another employee's draft exists."""
    test_client, _, second, _ = client
    created = _create_session(test_client)
    test_client.app.dependency_overrides[get_current_user] = lambda: second

    detail = test_client.get(f"/api/intake/sessions/{created['id']}")
    listing = test_client.get("/api/intake/sessions")

    assert detail.status_code == 404
    assert listing.status_code == 200
    assert listing.json() == []


def test_repeat_delivery_starts_from_barcode_without_new_photo(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
) -> None:
    """A known item is identified first and never forced through a redundant photo step."""
    test_client, _, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)

    item_response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={
            "barcode": variant.barcode,
            "quantity": 10,
            "purchase_price": "125.50",
        },
    )
    supplier_response = test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )

    assert item_response.status_code == 201
    assert item_response.json()["kind"] == "existing_variant"
    assert item_response.json()["image_id"] is None
    assert item_response.json()["missing_requirements"] == []
    assert supplier_response.status_code == 200
    assert supplier_response.json()["missing_requirements"] == []
    assert session.scalars(select(Image)).all() == []
    events = session.scalars(select(ActivityEvent).order_by(ActivityEvent.occurred_at)).all()
    assert [event.event_type for event in events] == [
        ActivityEventType.INTAKE_SESSION_STARTED,
        ActivityEventType.INTAKE_ITEM_ADDED,
    ]
    assert events[1].data == {
        "session_id": intake_session["id"],
        "kind": "existing_variant",
    }


def test_new_product_must_start_with_photo_and_can_be_completed_later(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    session: Session,
) -> None:
    """Photo persists immediately; descriptive and commercial data remains resumable."""
    test_client, first, _, storage_root = client
    category, _, _ = catalog
    intake_session = _create_session(test_client)

    upload = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("rack.png", _png_bytes(), "image/png")},
    )

    assert upload.status_code == 201
    uploaded = upload.json()
    assert uploaded["kind"] == "new_product"
    assert uploaded["image_id"] is not None
    assert "missing_image" not in uploaded["missing_requirements"]
    assert set(uploaded["missing_requirements"]) == {
        "missing_category",
        "missing_product_title",
        "missing_quantity",
        "missing_purchase_price",
    }

    update = test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{uploaded['id']}",
        json={
            "category_id": str(category.id),
            "product_title": "New shoe rack",
            "variant_title": "Blue",
            "attributes": {"color": "blue"},
            "quantity": 3,
            "purchase_price": "1000",
        },
    )

    assert update.status_code == 200
    assert update.json()["missing_requirements"] == []
    image = session.get(Image, UUID(uploaded["image_id"]))
    assert image is not None
    assert image.created_by_id == first.id
    assert (storage_root / image.source_key).is_file()


def test_new_item_command_owns_single_rollback_when_image_metadata_fails(
    client: tuple[TestClient, User, User, Path],
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Intake command, not nested Media, owns rollback and file compensation."""
    test_client, first, _, storage_root = client
    intake_session = _create_session(test_client)
    service = IntakeDraftWorkflow(
        session,
        ImageService(session, storage=LocalImageStorage(storage_root)),
    )
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
        service.add_new_item(
            UUID(intake_session["id"]),
            "failed.png",
            _png_bytes(),
            actor_id=first.id,
        )

    assert rollback_calls == 1
    assert session.scalars(select(Image)).all() == []
    assert session.scalars(select(IntakeItemDraft)).all() == []
    assert not [path for path in storage_root.rglob("*") if path.is_file()]


def test_new_variant_requires_photo_but_reuses_existing_product(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
) -> None:
    """A new color starts from its own photo and does not duplicate the Product family."""
    test_client, _, _, _ = client
    _, product, _ = catalog
    intake_session = _create_session(test_client)

    response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        data={"product_id": str(product.id)},
        files={"file": ("green.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 201
    assert response.json()["kind"] == "new_variant"
    assert response.json()["product_id"] == str(product.id)
    assert set(response.json()["missing_requirements"]) == {
        "missing_variant_title",
        "missing_quantity",
        "missing_purchase_price",
    }
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{response.json()['id']}",
        json={
            "variant_title": "Green",
            "quantity": 2,
            "purchase_price": "450",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )

    completion = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert completion.status_code == 200
    assert completion.json()["items"][0]["product_id"] == str(product.id)
    variants = session.scalars(
        select(CatalogVariant).where(CatalogVariant.product_id == product.id)
    ).all()
    assert len(variants) == 2


def test_invalid_photo_creates_neither_image_nor_item(
    client: tuple[TestClient, User, User, Path],
    session: Session,
) -> None:
    """Rejected bytes cannot leave a misleading operational draft or source file."""
    test_client, _, _, storage_root = client
    intake_session = _create_session(test_client)

    response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("fake.png", b"not an image", "image/png")},
    )

    assert response.status_code == 415
    assert session.scalars(select(Image)).all() == []
    assert session.scalars(select(IntakeItemDraft)).all() == []
    assert not storage_root.exists()


def test_abandoned_session_is_preserved_but_cannot_be_changed(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    session: Session,
) -> None:
    """Explicitly abandoned work remains visible while becoming immutable."""
    test_client, _, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)

    abandoned = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/abandon",
        json={"reason": "Packaging was empty"},
    )
    mutation = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={"variant_id": str(variant.id)},
    )
    filtered = test_client.get(
        "/api/intake/sessions",
        params={"session_status": "abandoned"},
    )

    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"
    assert abandoned.json()["abandonment_reason"] == "Packaging was empty"
    assert mutation.status_code == 409
    assert filtered.status_code == 200
    assert [row["id"] for row in filtered.json()] == [intake_session["id"]]
    event = session.scalar(
        select(ActivityEvent).where(
            ActivityEvent.event_type == ActivityEventType.INTAKE_SESSION_ABANDONED
        )
    )
    assert event is not None
    assert event.data["reason"] == "Packaging was empty"
    assert int(event.data["duration_seconds"]) >= 0


def test_abandoned_item_creates_one_attributed_activity_fact(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    session: Session,
) -> None:
    """Explicit item abandonment is visible without recording every draft edit."""
    test_client, first, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)
    item = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={"variant_id": str(variant.id)},
    ).json()

    response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}/abandon",
        json={"reason": "Wrong package"},
    )

    assert response.status_code == 200
    event = session.scalar(
        select(ActivityEvent).where(
            ActivityEvent.event_type == ActivityEventType.INTAKE_ITEM_ABANDONED
        )
    )
    assert event is not None
    assert event.actor_id == first.id
    assert event.entity_id == UUID(item["id"])
    assert event.data == {
        "session_id": intake_session["id"],
        "reason": "Wrong package",
    }


def test_existing_item_requires_exactly_one_identifier(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
) -> None:
    """Scanner and internal identifiers cannot compete in one request."""
    test_client, _, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)

    response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={"variant_id": str(variant.id), "barcode": variant.barcode},
    )

    assert response.status_code == 422


def test_complete_existing_variant_posts_receipt_and_is_idempotent(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
) -> None:
    """One completion posts stock once; a retry reconstructs the same business result."""
    test_client, first, _, _ = client
    _, product, variant = catalog
    intake_session = _create_session(test_client)
    test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={
            "barcode": variant.barcode,
            "quantity": 10,
            "rental_quantity": 2,
            "purchase_price": "125.50",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )

    first_completion = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")
    second_completion = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert first_completion.status_code == 200
    assert second_completion.status_code == 200
    assert second_completion.json() == first_completion.json()
    result = first_completion.json()
    assert result["receipt"]["status"] == "posted"
    assert result["items"][0]["product_id"] == str(product.id)
    assert result["items"][0]["variant_id"] == str(variant.id)
    assert set(result["readiness"][0]["missing_requirements"]) == {
        "missing_primary_image",
        "missing_retail_price",
    }
    receipts = session.scalars(select(Receipt)).all()
    receipt_items = session.scalars(select(ReceiptItem)).all()
    movements = session.scalars(select(StockMovement)).all()
    rental_assets = session.scalars(
        select(RentalAssetRecord).order_by(RentalAssetRecord.asset_number)
    ).all()
    assert len(receipts) == len(receipt_items) == len(movements) == 1
    assert len(rental_assets) == 2
    assert receipt_items[0].quantity == 10
    assert movements[0].quantity_delta == 10
    assert movements[0].created_by_id == first.id
    assert [asset.asset_number for asset in rental_assets] == ["RENT-000001", "RENT-000002"]
    assert all(asset.variant_id == variant.id for asset in rental_assets)
    assert all(asset.purpose is AssetPurpose.RENTAL for asset in rental_assets)
    assert all(asset.condition is AssetCondition.NEW for asset in rental_assets)
    assert all(asset.availability is RentalAvailability.AVAILABLE for asset in rental_assets)
    assert all(asset.created_by_id == first.id for asset in rental_assets)
    assert RentalAssetRepository(session).get_by_asset_number("rent-000001") is not None
    assert RentalAssetRepository(session).get_by_asset_number("RENT-000002") is not None
    completed = session.get(IntakeSession, UUID(intake_session["id"]))
    assert completed is not None
    assert completed.status.value == "completed"
    assert completed.receipt_id == receipts[0].id
    completed_events = session.scalars(
        select(ActivityEvent).where(
            ActivityEvent.event_type == ActivityEventType.INTAKE_SESSION_COMPLETED
        )
    ).all()
    assert len(completed_events) == 1
    assert completed_events[0].data["receipt_id"] == str(receipts[0].id)
    assert completed_events[0].data["item_count"] == 1
    assert completed_events[0].data["total_quantity"] == 10
    assert int(completed_events[0].data["duration_seconds"]) >= 0


def test_optional_retail_price_is_created_atomically_during_intake(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
) -> None:
    """An entered sale price becomes a Pricing fact while omission remains valid elsewhere."""
    test_client, first, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)
    item = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={
            "variant_id": str(variant.id),
            "quantity": 1,
            "purchase_price": "500",
            "retail_price": "999.90",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )

    response = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert item.status_code == 201
    assert item.json()["retail_price"] == "999.90"
    assert response.status_code == 200
    assert response.json()["readiness"][0]["missing_requirements"] == [
        "missing_primary_image"
    ]
    prices = session.scalars(select(Price)).all()
    assert len(prices) == 1
    assert prices[0].variant_id == variant.id
    assert prices[0].price_type is PriceType.RETAIL
    assert prices[0].amount == Decimal("999.90")
    assert prices[0].created_by_id == first.id


def test_intake_rejects_rental_quantity_above_received_quantity(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
) -> None:
    """A draft cannot allocate more physical assets than the units received."""
    test_client, _, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)

    create_response = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={
            "variant_id": str(variant.id),
            "quantity": 2,
            "rental_quantity": 3,
        },
    )

    assert create_response.status_code == 422

    item = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={"variant_id": str(variant.id), "quantity": 3},
    ).json()
    update_response = test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}",
        json={"rental_quantity": 4},
    )

    assert update_response.status_code == 400
    assert update_response.json()["detail"] == (
        "Rental quantity cannot exceed received quantity."
    )


def test_complete_new_product_creates_primary_image_catalog_and_stock(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Photo First draft becomes one catalog position and one posted Receipt atomically."""
    test_client, first, _, _ = client
    category, _, _ = catalog
    intake_session = _create_session(test_client)
    upload = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        data={"manufacturer_barcode": "4601234567893"},
        files={"file": ("new.png", _png_bytes(), "image/png")},
    ).json()
    assert upload["manufacturer_barcode"] == "4601234567893"
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{upload['id']}",
        json={
            "category_id": str(category.id),
            "product_title": "Brand new rack",
            "variant_title": "White",
            "quantity": 4,
            "purchase_price": "700.00",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)

    response = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert response.status_code == 200
    assert commit_calls == 1
    result = response.json()
    assert result["receipt"]["status"] == "posted"
    assert result["readiness"][0]["missing_requirements"] == ["missing_retail_price"]
    created_variant = session.get(CatalogVariant, UUID(result["items"][0]["variant_id"]))
    created_product = session.get(CatalogProduct, UUID(result["items"][0]["product_id"]))
    assert created_variant is not None
    assert created_product is not None
    assert created_product.title == "Brand new rack"
    assert created_variant.title == "White"
    assert created_variant.created_by_id == first.id
    assert {(row.value, row.source.value) for row in created_variant.barcodes} == {
        (created_variant.barcode, "internal"),
        ("4601234567893", "manufacturer"),
    }
    found = test_client.get(
        "/api/catalog/variants/lookup/by-barcode",
        params={"barcode": "4601234567893"},
    )
    assert found.status_code == 200
    assert found.json()["id"] == str(created_variant.id)
    repeat_session = _create_session(test_client)
    repeated = test_client.post(
        f"/api/intake/sessions/{repeat_session['id']}/items/existing",
        json={"barcode": "4601234567893"},
    )
    assert repeated.status_code == 201
    assert repeated.json()["variant_id"] == str(created_variant.id)
    link = session.scalar(select(ImageLink).where(ImageLink.entity_id == created_variant.id))
    assert link is not None
    assert link.image_id == UUID(upload["image_id"])
    assert link.role is ImageLinkRole.PRIMARY
    movement = session.scalar(
        select(StockMovement).where(StockMovement.variant_id == created_variant.id)
    )
    assert movement is not None
    assert movement.quantity_delta == 4


def test_complete_new_product_with_multiple_variants_uses_one_product_and_separate_stock(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
) -> None:
    """One draft Product can own several Variant facts without duplicating the Product."""
    test_client, _, _, _ = client
    category, _, _ = catalog
    intake_session = _create_session(test_client)
    root = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("product.png", _png_bytes(), "image/png")},
    ).json()
    child = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        data={"draft_product_item_id": root["id"]},
    )
    assert child.status_code == 201
    assert child.json()["image_id"] is None
    assert child.json()["draft_product_item_id"] == root["id"]
    assert root["reserved_sku"].startswith("SKU-")
    assert len(root["reserved_internal_barcode"]) == 13
    assert child.json()["reserved_internal_barcode"] != root["reserved_internal_barcode"]
    for item, title, quantity, price in (
        (root, "Pepsi", 8, "299"),
        (child.json(), "Fanta", 11, "319"),
    ):
        payload = {
            "variant_title": title,
            "quantity": quantity,
            "purchase_price": "140",
            "retail_price": price,
        }
        if item["id"] == root["id"]:
            payload.update(
                category_id=str(category.id),
                product_title="Needle Can",
                product_description="Canned drinks",
            )
        response = test_client.patch(
            f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}",
            json=payload,
        )
        assert response.status_code == 200
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )

    completed = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert completed.status_code == 200
    result = completed.json()
    assert len(result["items"]) == 2
    assert len({item["product_id"] for item in result["items"]}) == 1
    variant_ids = [UUID(item["variant_id"]) for item in result["items"]]
    variants = session.scalars(
        select(CatalogVariant).where(CatalogVariant.id.in_(variant_ids))
    ).all()
    assert {variant.title for variant in variants} == {"Pepsi", "Fanta"}
    assert {variant.barcode for variant in variants} == {
        root["reserved_internal_barcode"],
        child.json()["reserved_internal_barcode"],
    }
    movements = session.scalars(
        select(StockMovement).where(StockMovement.variant_id.in_(variant_ids))
    ).all()
    assert sorted(movement.quantity_delta for movement in movements) == [8, 11]
    prices = session.scalars(select(Price).where(Price.variant_id.in_(variant_ids))).all()
    assert {price.amount for price in prices} == {Decimal("299.00"), Decimal("319.00")}


def test_saved_new_variant_label_is_available_before_complete(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
) -> None:
    """Stable draft identity makes exact-size labels independent from Inventory posting."""
    test_client, _, _, _ = client
    category, _, _ = catalog
    intake_session = _create_session(test_client)
    item = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("product.png", _png_bytes(), "image/png")},
    ).json()
    saved = test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}",
        json={
            "category_id": str(category.id),
            "product_title": "Needle Can",
            "variant_title": "Dr Pepper",
            "quantity": 5,
            "purchase_price": "140",
        },
    )

    label = test_client.get(
        f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}/labels/40x30.pdf",
        params={"quantity": 5},
    )

    assert saved.status_code == 200
    assert label.status_code == 200
    assert label.headers["content-type"] == "application/pdf"
    assert label.content.startswith(b"%PDF")


def test_draft_photo_can_be_replaced_before_complete(
    client: tuple[TestClient, User, User, Path],
    session: Session,
) -> None:
    """Draft photo replacement keeps immutable Media metadata and changes no stock facts."""
    test_client, _, _, _ = client
    intake_session = _create_session(test_client)
    item = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("first.png", _png_bytes(), "image/png")},
    ).json()

    replaced = test_client.put(
        f"/api/intake/sessions/{intake_session['id']}/items/{item['id']}/image",
        files={"file": ("replacement.png", _png_bytes(), "image/png")},
    )

    assert replaced.status_code == 200
    assert replaced.json()["image_id"] != item["image_id"]
    previous = session.get(Image, UUID(item["image_id"]))
    assert previous is not None
    assert previous.deleted_at is not None
    assert session.scalars(select(StockMovement)).all() == []


def test_draft_product_cannot_be_removed_before_its_variants(
    client: tuple[TestClient, User, User, Path],
) -> None:
    """Draft cleanup preserves the Product root while active Variant drafts depend on it."""
    test_client, _, _, _ = client
    intake_session = _create_session(test_client)
    root = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("product.png", _png_bytes(), "image/png")},
    ).json()
    child = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        data={"draft_product_item_id": root["id"]},
    ).json()

    blocked = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/{root['id']}/abandon",
        json={"reason": "Wrong product"},
    )
    removed_child = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/{child['id']}/abandon",
        json={"reason": "Wrong variant"},
    )
    removed_root = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/{root['id']}/abandon",
        json={"reason": "Wrong product"},
    )

    assert blocked.status_code == 409
    assert removed_child.status_code == 200
    assert removed_root.status_code == 200


def test_incomplete_session_cannot_create_receipt(
    client: tuple[TestClient, User, User, Path],
    session: Session,
) -> None:
    """Completion validation happens before any formal warehouse document is created."""
    test_client, _, _, _ = client
    intake_session = _create_session(test_client)

    response = test_client.post(f"/api/intake/sessions/{intake_session['id']}/complete")

    assert response.status_code == 409
    assert response.json()["detail"] == "Intake session is incomplete."
    assert session.scalars(select(Receipt)).all() == []
    persisted = session.get(IntakeSession, UUID(intake_session["id"]))
    assert persisted is not None
    assert persisted.status.value == "draft"


def test_late_completion_failure_rolls_back_catalog_receipt_and_ledger_but_keeps_draft(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late posting failure keeps the source photo and resumable input as the only facts."""
    test_client, first, _, storage_root = client
    category, _, _ = catalog
    intake_session = _create_session(test_client)
    upload = test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/new",
        files={"file": ("rollback.png", _png_bytes(), "image/png")},
    ).json()
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}/items/{upload['id']}",
        json={
            "category_id": str(category.id),
            "product_title": "Rollback product",
            "variant_title": "Rollback variant",
            "quantity": 2,
            "rental_quantity": 1,
            "purchase_price": "300",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )
    original_product_count = len(session.scalars(select(CatalogProduct)).all())
    original_variant_count = len(session.scalars(select(CatalogVariant)).all())
    original_commit = session.commit
    original_rollback = session.rollback
    commit_calls = 0
    rollback_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    def count_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    def fail_readiness(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated late completion failure")

    service = CompleteIntakeWorkflow(session)
    monkeypatch.setattr(session, "commit", count_commit)
    monkeypatch.setattr(session, "rollback", count_rollback)
    monkeypatch.setattr(service._readiness_service, "check_variant", fail_readiness)

    with pytest.raises(RuntimeError, match="simulated late completion failure"):
        service.complete(UUID(intake_session["id"]), actor_id=first.id)

    assert commit_calls == 0
    assert rollback_calls == 1
    assert len(session.scalars(select(CatalogProduct)).all()) == original_product_count
    assert len(session.scalars(select(CatalogVariant)).all()) == original_variant_count
    assert session.scalars(select(Receipt)).all() == []
    assert session.scalars(select(ReceiptItem)).all() == []
    assert session.scalars(select(StockMovement)).all() == []
    assert session.scalars(select(RentalAssetRecord)).all() == []
    assert session.scalars(select(ImageLink)).all() == []
    assert (
        session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.event_type == ActivityEventType.INTAKE_SESSION_COMPLETED
            )
        ).all()
        == []
    )
    persisted = session.get(IntakeSession, UUID(intake_session["id"]))
    assert persisted is not None
    assert persisted.status.value == "draft"
    image = session.get(Image, UUID(upload["image_id"]))
    assert image is not None
    assert (storage_root / image.source_key).is_file()


def test_completion_owns_single_rollback_when_nested_receipt_posting_fails(
    client: tuple[TestClient, User, User, Path],
    catalog: tuple[Category, CatalogProduct, CatalogVariant],
    supplier: Supplier,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested posting propagates failure without finalizing the Intake transaction."""
    test_client, first, _, _ = client
    _, _, variant = catalog
    intake_session = _create_session(test_client)
    test_client.post(
        f"/api/intake/sessions/{intake_session['id']}/items/existing",
        json={
            "barcode": variant.barcode,
            "quantity": 2,
            "purchase_price": "300",
        },
    )
    test_client.patch(
        f"/api/intake/sessions/{intake_session['id']}",
        json={"supplier_id": str(supplier.id)},
    )
    workflow = CompleteIntakeWorkflow(session)
    original_rollback = session.rollback
    rollback_calls = 0

    def fail_movement(*args: object, **kwargs: object) -> None:
        raise RuntimeError("nested movement failure")

    def count_rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback()

    monkeypatch.setattr(
        workflow._posting_service._inventory_service,
        "create_movement",
        fail_movement,
    )
    monkeypatch.setattr(session, "rollback", count_rollback)

    with pytest.raises(RuntimeError, match="nested movement failure"):
        workflow.complete(UUID(intake_session["id"]), actor_id=first.id)

    assert rollback_calls == 1
    assert session.scalars(select(Receipt)).all() == []
    assert session.scalars(select(ReceiptItem)).all() == []
    assert session.scalars(select(StockMovement)).all() == []

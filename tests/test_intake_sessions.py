from __future__ import annotations

from collections.abc import Generator
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
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
from core.pricing.models import Price
from core.receipt.models import Receipt, ReceiptItem
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
            Image.__table__,
            ImageLink.__table__,
            Supplier.__table__,
            Receipt.__table__,
            ReceiptItem.__table__,
            StockMovement.__table__,
            Price.__table__,
            IntakeSession.__table__,
            IntakeItemDraft.__table__,
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


def test_session_starts_before_supplier_and_is_resumable(
    client: tuple[TestClient, User, User, Path],
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
        "missing_variant_title",
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
    assert len(receipts) == len(receipt_items) == len(movements) == 1
    assert receipt_items[0].quantity == 10
    assert movements[0].quantity_delta == 10
    assert movements[0].created_by_id == first.id
    completed = session.get(IntakeSession, UUID(intake_session["id"]))
    assert completed is not None
    assert completed.status.value == "completed"
    assert completed.receipt_id == receipts[0].id


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
        files={"file": ("new.png", _png_bytes(), "image/png")},
    ).json()
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
    link = session.scalar(select(ImageLink).where(ImageLink.entity_id == created_variant.id))
    assert link is not None
    assert link.image_id == UUID(upload["image_id"])
    assert link.role is ImageLinkRole.PRIMARY
    movement = session.scalar(
        select(StockMovement).where(StockMovement.variant_id == created_variant.id)
    )
    assert movement is not None
    assert movement.quantity_delta == 4


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
    assert session.scalars(select(ImageLink)).all() == []
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

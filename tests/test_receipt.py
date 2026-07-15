from __future__ import annotations

import re
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.main import create_app
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.receipt.schemas import ReceiptCreate, ReceiptItemCreate, ReceiptItemUpdate, ReceiptUpdate
from core.receipt.service import (
    ReceiptItemService,
    ReceiptNotDraftError,
    ReceiptService,
    ReceiptSupplierError,
    ReceiptVariantError,
)
from core.shared.db import Base
from core.supplier.models import Supplier


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for receipt draft tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Supplier.__table__,
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            Receipt.__table__,
            ReceiptItem.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def supplier(session: Session) -> Supplier:
    """Create an active supplier suitable for receiving drafts."""
    value = Supplier(name="Acme", code="SUP-000001")
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create an active catalog variant suitable for receipt items."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Film camera", slug="film-camera", category_id=category.id)
    session.add(product)
    session.flush()
    value = CatalogVariant(product_id=product.id, title="Black body", sku="SKU-000001")
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the receipt test database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(session: Session) -> User:
    """Create an active account for authenticated receipt requests."""
    return IdentityService(session).create_admin(
        "receipt-admin@example.com",
        "Receipt Admin",
        "long enough password",
    )


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Authenticate a test user and return a bearer authorization header."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def receipt_payload(supplier: Supplier) -> ReceiptCreate:
    """Build a valid receipt opening payload for a supplier."""
    return ReceiptCreate(
        supplier_id=supplier.id,
        receipt_date=date(2026, 7, 15),
        source_document_number="  INV-42  ",
    )


def test_open_receipt_generates_draft_number_and_trims_source_document(
    session: Session,
    supplier: Supplier,
) -> None:
    """New receipts receive stable numbers and start in the draft lifecycle state."""
    receipt = ReceiptService(session).open_receipt(receipt_payload(supplier))

    assert receipt.number == "REC-000001"
    assert receipt.status is ReceiptStatus.DRAFT
    assert receipt.source_document_number == "INV-42"
    assert receipt.created_by_id is None


def test_receipt_schemas_forbid_public_number_and_status() -> None:
    """Public receipt writes cannot set generated numbers or lifecycle status."""
    assert "number" not in ReceiptCreate.model_fields
    assert "number" not in ReceiptUpdate.model_fields
    assert "status" not in ReceiptCreate.model_fields
    assert "status" not in ReceiptUpdate.model_fields
    with pytest.raises(ValidationError):
        ReceiptCreate(
            supplier_id="019f64e4-a309-742b-b1ca-b6059d31bce5",
            receipt_date="2026-07-15",
            number="REC-999999",
        )


def test_receipt_requires_active_non_archived_supplier(
    session: Session, supplier: Supplier
) -> None:
    """Receipt drafts reject inactive and archived suppliers."""
    service = ReceiptService(session)
    supplier.is_active = False
    session.commit()
    with pytest.raises(ReceiptSupplierError):
        service.open_receipt(receipt_payload(supplier))

    supplier.is_active = True
    supplier.soft_delete()
    session.commit()
    with pytest.raises(ReceiptSupplierError):
        service.open_receipt(receipt_payload(supplier))


def test_draft_receipt_update_archive_and_system_attribution(
    session: Session,
    supplier: Supplier,
) -> None:
    """System draft operations preserve null attribution and hide archived receipts."""
    service = ReceiptService(session)
    receipt = service.open_receipt(receipt_payload(supplier))
    updated = service.update_draft(
        receipt.id,
        ReceiptUpdate(source_document_number="  INV-43  ", notes="Checked"),
    )
    service.archive_draft(receipt.id)

    assert updated.number == "REC-000001"
    assert updated.source_document_number == "INV-43"
    assert receipt.created_by_id is None
    assert receipt.updated_by_id is None
    assert receipt.deleted_by_id is None
    assert service.list_receipts() == []


def test_non_draft_receipts_cannot_be_updated_or_archived(
    session: Session, supplier: Supplier
) -> None:
    """Draft-only lifecycle protection rejects future posted receipt mutations."""
    service = ReceiptService(session)
    receipt = service.open_receipt(receipt_payload(supplier))
    receipt.status = ReceiptStatus.POSTED
    session.commit()

    with pytest.raises(ReceiptNotDraftError):
        service.update_draft(receipt.id, ReceiptUpdate(notes="No longer editable"))
    with pytest.raises(ReceiptNotDraftError):
        service.archive_draft(receipt.id)


def test_receipt_items_require_active_variant_and_decimal_prices(
    session: Session,
    supplier: Supplier,
    variant: CatalogVariant,
) -> None:
    """Draft lines require active variants and store quantized Decimal unit prices."""
    receipt = ReceiptService(session).open_receipt(receipt_payload(supplier))
    item_service = ReceiptItemService(session)
    item = item_service.add_item(
        receipt.id,
        ReceiptItemCreate(variant_id=variant.id, quantity=2, purchase_price=Decimal("10.125")),
    )

    assert item.purchase_price == Decimal("10.13")
    assert item.quantity == 2
    assert item.created_by_id is None
    with pytest.raises(ValidationError):
        ReceiptItemCreate(variant_id=variant.id, quantity=1, purchase_price=1.5)
    with pytest.raises(ValidationError):
        ReceiptItemCreate(variant_id=variant.id, quantity=0, purchase_price="1.00")
    with pytest.raises(ValidationError):
        ReceiptItemCreate(variant_id=variant.id, quantity=1, purchase_price="-0.01")

    variant.is_active = False
    session.commit()
    with pytest.raises(ReceiptVariantError):
        item_service.add_item(
            receipt.id,
            ReceiptItemCreate(variant_id=variant.id, quantity=1, purchase_price="0"),
        )


def test_draft_item_update_remove_and_duplicate_variant_lines(
    session: Session,
    supplier: Supplier,
    variant: CatalogVariant,
) -> None:
    """Draft lines can repeat variants and change without creating stock records."""
    receipt = ReceiptService(session).open_receipt(receipt_payload(supplier))
    service = ReceiptItemService(session)
    first = service.add_item(
        receipt.id,
        ReceiptItemCreate(variant_id=variant.id, quantity=1, purchase_price="0"),
    )
    second = service.add_item(
        receipt.id,
        ReceiptItemCreate(variant_id=variant.id, quantity=3, purchase_price="5.00"),
    )
    updated = service.update_item(
        receipt.id,
        first.id,
        ReceiptItemUpdate(quantity=2, purchase_price="2.505"),
    )
    service.remove_item(receipt.id, second.id)

    assert updated.quantity == 2
    assert updated.purchase_price == Decimal("2.51")
    assert [item.id for item in service.list_items(receipt.id)] == [first.id]
    assert second.deleted_at is not None
    assert not any("stock" in table_name for table_name in Base.metadata.tables)


def test_receipt_routes_require_authentication(client: TestClient) -> None:
    """Receipt and receipt item endpoints reject anonymous requests."""
    assert client.get("/api/receipts").status_code == 401
    assert client.post("/api/receipts", json={}).status_code == 401


def test_authenticated_receipt_and_item_routes_attribute_writes(
    client: TestClient,
    session: Session,
    supplier: Supplier,
    user: User,
    variant: CatalogVariant,
) -> None:
    """Authenticated draft receipt and line writes record the acting user."""
    headers = authorization_header(client, user)
    created = client.post(
        "/api/receipts",
        headers=headers,
        json={"supplier_id": str(supplier.id), "receipt_date": "2026-07-15"},
    )
    receipt = session.get(Receipt, UUID(created.json()["id"]))
    assert created.status_code == 201
    assert receipt is not None
    assert receipt.created_by_id == user.id

    updated = client.patch(
        f"/api/receipts/{receipt.id}",
        headers=headers,
        json={"notes": "Verified"},
    )
    session.refresh(receipt)
    assert updated.status_code == 200
    assert receipt.updated_by_id == user.id

    added = client.post(
        f"/api/receipts/{receipt.id}/items",
        headers=headers,
        json={"variant_id": str(variant.id), "quantity": 1, "purchase_price": "0"},
    )
    item = session.get(ReceiptItem, UUID(added.json()["id"]))
    assert added.status_code == 201
    assert item is not None
    assert item.created_by_id == user.id

    removed = client.delete(f"/api/receipts/{receipt.id}/items/{item.id}", headers=headers)
    session.refresh(item)
    assert removed.status_code == 204
    assert item.deleted_by_id == user.id

    archived = client.delete(f"/api/receipts/{receipt.id}", headers=headers)
    session.refresh(receipt)
    assert archived.status_code == 204
    assert receipt.deleted_by_id == user.id


def test_receipt_migration_revision_identifier_is_short() -> None:
    """Receipt migration revision identifiers remain within the project Alembic limit."""
    migration_path = Path("migrations/versions/0009_receipt_drafts.py")
    match = re.search(r'^revision: str = "([^"]+)"$', migration_path.read_text(), re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32

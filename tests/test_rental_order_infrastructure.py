from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant
from core.customers.customer import Customer
from core.customers.models import CustomerRecord
from core.customers.schemas import CustomerCreate
from core.customers.service import CustomerService
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.main import create_app
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.exceptions import RentalDomainError
from core.rental.mapper import rental_order_from_record, rental_order_to_record
from core.rental.models import RentalAssetRecord, RentalOrderItemRecord, RentalOrderRecord
from core.rental.order import RentalOrder
from core.rental.order_admin import RentalOrderAdmin, RentalOrderItemAdmin
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.rental.order_schemas import (
    RentalOrderCreate,
    RentalOrderItemCreate,
    RentalOrderItemReturn,
)
from core.rental.order_service import RentalOrderService
from core.rental.repository import RentalOrderRepository
from core.shared.db import Base, UUIDv7, generate_uuid_v7

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
def session() -> Generator[Session]:
    """Provide isolated persistence for RentalOrder workflows."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            CustomerRecord.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            RentalAssetRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def customer(session: Session) -> Customer:
    """Create an active customer used by order workflows."""
    return CustomerService(session).create_customer(
        CustomerCreate(full_name="Иван Иванов", phone="+79991234567")
    )


@pytest.fixture
def asset(session: Session) -> RentalAssetRecord:
    """Persist one available physical asset with a resolvable title snapshot."""
    product_id = generate_uuid_v7()
    variant_id = generate_uuid_v7()
    session.add(
        CatalogProduct(
            id=product_id,
            title="Инструменты",
            slug="tools",
            category_id=generate_uuid_v7(),
        )
    )
    session.add(
        CatalogVariant(
            id=variant_id,
            product_id=product_id,
            title="Шуруповерт Bosch",
            sku="RENTAL-TEST-001",
            barcode="2000000000000000000001",
            attributes={},
        )
    )
    record = RentalAssetRecord(
        id=generate_uuid_v7(),
        asset_number="RENT-000001",
        variant_id=variant_id,
        intake_item_id=generate_uuid_v7(),
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(record)
    session.commit()
    return record


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide an API client sharing the isolated RentalOrder database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _draft(service: RentalOrderService, customer_id: UUIDv7) -> RentalOrder:
    """Create a valid empty draft for focused infrastructure tests."""
    return service.create_draft(
        RentalOrderCreate(
            customer_id=customer_id,
            planned_start_at=NOW,
            planned_return_at=NOW + timedelta(days=2),
            deposit_amount=Decimal("1000"),
        )
    )


def _add_asset(
    service: RentalOrderService,
    order: RentalOrder,
    asset: RentalAssetRecord,
) -> RentalOrder:
    """Add the shared test asset to an existing draft."""
    return service.add_item(
        order.id,
        RentalOrderItemCreate(
            rental_asset_id=asset.id,
            agreed_price=Decimal("700"),
            discount=Decimal("50"),
        ),
    )


def test_mapper_round_trip_restores_complete_aggregate(customer: Customer) -> None:
    """Persistence mapping retains snapshots, money, status, and owned items."""
    order = RentalOrder.create(
        order_id=generate_uuid_v7(),
        order_number="RORD-000001",
        customer_id=customer.id,
        customer_name_snapshot=customer.full_name,
        customer_phone_snapshot=customer.phone,
        planned_start_at=NOW,
        planned_return_at=NOW + timedelta(days=1),
        deposit_amount=Decimal("500"),
    )
    order.add_item(
        item_id=generate_uuid_v7(),
        rental_asset_id=generate_uuid_v7(),
        asset_number_snapshot="RENT-000001",
        title_snapshot="Шуруповерт Bosch",
        agreed_price=Decimal("700"),
        discount=Decimal("50"),
    )

    restored = rental_order_from_record(rental_order_to_record(order))

    assert restored.order_number == order.order_number
    assert restored.deposit_amount == Decimal("500.00")
    assert restored.items[0].title_snapshot == "Шуруповерт Bosch"
    assert restored.items[0].discount == Decimal("50.00")


def test_repository_supports_business_number_and_search(
    session: Session,
    customer: Customer,
) -> None:
    """Repository reads aggregate projections without committing."""
    service = RentalOrderService(session)
    order = _draft(service, customer.id)
    repository = RentalOrderRepository(session)

    by_number = repository.get_by_number(order.order_number)
    found = repository.search("Иван")

    assert by_number is not None
    assert by_number.id == order.id
    assert [record.id for record in found] == [order.id]


def test_service_issues_and_returns_order_atomically(
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """Application workflows coordinate order and physical asset state."""
    service = RentalOrderService(session)
    order = _add_asset(service, _draft(service, customer.id), asset)

    issued = service.issue(order.id)
    session.refresh(asset)
    assert issued.status is RentalOrderStatus.ISSUED
    assert asset.availability is RentalAvailability.RENTED

    returned = service.return_item(
        order.id,
        issued.items[0].id,
        RentalOrderItemReturn(
            condition=AssetCondition.GOOD,
            charged_amount=Decimal("650"),
            returned_at=NOW + timedelta(days=2),
        ),
    )
    session.refresh(asset)
    assert returned.status is RentalOrderStatus.CLOSED
    assert returned.items[0].status is RentalOrderItemStatus.RETURNED
    assert asset.availability is RentalAvailability.AVAILABLE


def test_service_rejects_unavailable_asset_before_adding_item(
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """An unavailable physical asset cannot enter a checkout draft."""
    asset.availability = RentalAvailability.RENTED
    session.commit()
    service = RentalOrderService(session)
    order = _draft(service, customer.id)

    with pytest.raises(RentalDomainError):
        _add_asset(service, order, asset)

    assert service.get(order.id).items == ()


def test_api_supports_authenticated_rental_order_workflow(
    client: TestClient,
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """Operator can create, fill, issue, return, and retrieve an order."""
    user = IdentityService(session).create_admin(
        "rental-admin@example.com",
        "Rental Admin",
        "long enough password",
    )
    login = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = client.post(
        "/rental/orders",
        headers=headers,
        json={
            "customer_id": str(customer.id),
            "planned_start_at": NOW.isoformat(),
            "planned_return_at": (NOW + timedelta(days=2)).isoformat(),
            "deposit_amount": "1000",
        },
    )
    assert created.status_code == 201
    order_id = created.json()["id"]

    added = client.post(
        f"/rental/orders/{order_id}/items",
        headers=headers,
        json={
            "rental_asset_id": str(asset.id),
            "agreed_price": "700",
            "discount": "50",
        },
    )
    assert added.status_code == 200
    item_id = added.json()["items"][0]["id"]
    issued = client.post(f"/rental/orders/{order_id}/issue", headers=headers)
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    persisted_order = session.get(RentalOrderRecord, UUID(order_id))
    assert persisted_order is not None
    assert persisted_order.issued_by_id == user.id

    returned = client.post(
        f"/rental/orders/{order_id}/items/{item_id}/return",
        headers=headers,
        json={"condition": "good", "charged_amount": "650"},
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "closed"
    assert client.get(f"/rental/orders/{order_id}", headers=headers).status_code == 200


def test_api_searches_rental_assets_for_checkout(
    client: TestClient,
    session: Session,
    asset: RentalAssetRecord,
) -> None:
    """Checkout UI can find an asset and inspect its operational availability."""
    user = IdentityService(session).create_admin(
        "asset-search@example.com",
        "Asset Search",
        "long enough password",
    )
    login = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get("/api/rental/assets?query=RENT-000001", headers=headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(asset.id),
            "asset_number": "RENT-000001",
            "variant_id": str(asset.variant_id),
            "product_title": "Инструменты",
            "variant_title": "Шуруповерт Bosch",
            "condition": "new",
            "availability": "available",
        }
    ]


def test_rental_order_routes_require_authentication(client: TestClient) -> None:
    """RentalOrder endpoints reject anonymous requests."""
    assert client.get("/rental/orders").status_code == 401
    assert client.post("/rental/orders", json={}).status_code == 401


def test_admin_and_migration_register_read_only_order_persistence() -> None:
    """Admin and Alembic expose infrastructure without bypassing the domain."""
    content = Path("migrations/versions/0019_create_rental_orders.py").read_text()
    match = re.search(r'^revision: str = "([^"]+)"$', content, re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32
    assert RentalOrderRecord.__tablename__ in Base.metadata.tables
    assert RentalOrderItemRecord.__tablename__ in Base.metadata.tables
    assert RentalOrderAdmin.can_create is False
    assert RentalOrderAdmin.can_edit is False
    assert RentalOrderItemAdmin.can_delete is False

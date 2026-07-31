from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PillowImage
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
from core.media.models import Image, ImageLink
from core.media.service import ImageService
from core.media.storage import LocalImageStorage
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.exceptions import RentalDomainError
from core.rental.lifecycle_routes import get_lifecycle_image_service
from core.rental.mapper import rental_order_from_record, rental_order_to_record
from core.rental.models import (
    RentalAssetRecord,
    RentalConditionPhotoRecord,
    RentalDamageRecord,
    RentalMaintenanceRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.order import RentalOrder
from core.rental.order_admin import RentalOrderAdmin, RentalOrderItemAdmin
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.rental.order_schemas import (
    RentalOrderCreate,
    RentalOrderItemCompletion,
    RentalOrderItemCreate,
    RentalOrderItemOutcome,
    RentalOrderItemReturn,
    RentalOrderItemsComplete,
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
            Image.__table__,
            ImageLink.__table__,
            Price.__table__,
            RentalAssetRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
            RentalMaintenanceRecord.__table__,
            RentalDamageRecord.__table__,
            RentalConditionPhotoRecord.__table__,
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
        acquisition_cost=Decimal("500"),
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
    asset: RentalAssetRecord,
) -> None:
    """Repository reads aggregate projections without committing."""
    service = RentalOrderService(session)
    order = _add_asset(service, _draft(service, customer.id), asset)
    repository = RentalOrderRepository(session)

    by_number = repository.get_by_number(order.order_number)
    found = repository.search("Иван")
    found_by_asset = repository.search("RENT-000001")

    assert by_number is not None
    assert by_number.id == order.id
    assert [record.id for record in found] == [order.id]
    assert [record.id for record in found_by_asset] == [order.id]


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


def test_service_completes_partial_return_and_lost_atomically(
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """Batch return preserves partial state and LOST never makes an asset available."""
    second_asset = RentalAssetRecord(
        id=generate_uuid_v7(),
        asset_number="RENT-000002",
        variant_id=asset.variant_id,
        intake_item_id=generate_uuid_v7(),
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(second_asset)
    session.commit()
    operator = IdentityService(session).create_admin(
        "return-operator@example.com",
        "Return Operator",
        "long enough password",
    )
    service = RentalOrderService(session)
    order = _add_asset(service, _draft(service, customer.id), asset)
    order = _add_asset(service, order, second_asset)
    issued = service.issue(order.id, actor_id=operator.id)

    partial = service.complete_items(
        order.id,
        RentalOrderItemsComplete(
            items=[
                RentalOrderItemCompletion(
                    item_id=issued.items[0].id,
                    outcome=RentalOrderItemOutcome.RETURNED,
                    condition=AssetCondition.DAMAGED,
                    charged_amount=Decimal("650"),
                    completed_at=NOW + timedelta(days=2),
                    note="Повреждён корпус",
                )
            ]
        ),
        actor_id=operator.id,
    )

    session.refresh(asset)
    first_item_record = session.get(RentalOrderItemRecord, issued.items[0].id)
    assert partial.status is RentalOrderStatus.ISSUED
    assert asset.availability is RentalAvailability.MAINTENANCE
    assert first_item_record is not None
    assert first_item_record.completed_by_id == operator.id

    completed = service.complete_items(
        order.id,
        RentalOrderItemsComplete(
            items=[
                RentalOrderItemCompletion(
                    item_id=issued.items[1].id,
                    outcome=RentalOrderItemOutcome.LOST,
                    charged_amount=Decimal("5000"),
                    completed_at=NOW + timedelta(days=3),
                    note="Не возвращён клиентом",
                )
            ]
        ),
        actor_id=operator.id,
    )

    session.refresh(second_asset)
    second_item_record = session.get(RentalOrderItemRecord, issued.items[1].id)
    assert completed.status is RentalOrderStatus.CLOSED
    assert completed.items[1].status is RentalOrderItemStatus.LOST
    assert second_asset.availability is RentalAvailability.RENTED
    assert second_item_record is not None
    assert second_item_record.completed_by_id == operator.id


def test_batch_return_rolls_back_every_item_when_one_command_fails(
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """A bad item in a batch cannot leave a physical asset partially returned."""
    service = RentalOrderService(session)
    issued = service.issue(_add_asset(service, _draft(service, customer.id), asset).id)

    with pytest.raises(RentalDomainError):
        service.complete_items(
            issued.id,
            RentalOrderItemsComplete(
                items=[
                    RentalOrderItemCompletion(
                        item_id=issued.items[0].id,
                        outcome=RentalOrderItemOutcome.RETURNED,
                        condition=AssetCondition.GOOD,
                        charged_amount=Decimal("650"),
                    ),
                    RentalOrderItemCompletion(
                        item_id=generate_uuid_v7(),
                        outcome=RentalOrderItemOutcome.LOST,
                        charged_amount=Decimal("0"),
                    ),
                ]
            ),
        )

    session.refresh(asset)
    persisted = service.get(issued.id)
    assert persisted.status is RentalOrderStatus.ISSUED
    assert persisted.items[0].status is RentalOrderItemStatus.ISSUED
    assert asset.availability is RentalAvailability.RENTED


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
    assert issued.json()["is_overdue"] is True
    persisted_order = session.get(RentalOrderRecord, UUID(order_id))
    assert persisted_order is not None
    assert persisted_order.issued_by_id == user.id

    returned = client.post(
        f"/rental/orders/{order_id}/complete-items",
        headers=headers,
        json={
            "items": [
                {
                    "item_id": item_id,
                    "outcome": "returned",
                    "condition": "good",
                    "charged_amount": "650",
                }
            ]
        },
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "closed"
    persisted_item = session.get(RentalOrderItemRecord, UUID(item_id))
    assert persisted_item is not None
    assert persisted_item.completed_by_id == user.id
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


def test_operational_catalog_links_product_asset_and_current_rental(
    client: TestClient,
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
) -> None:
    """First-party read projections connect Product, RentalAsset, and active order."""
    user = IdentityService(session).create_admin(
        "operations@example.com",
        "Operations",
        "long enough password",
    )
    service = RentalOrderService(session)
    order = service.issue(
        _add_asset(service, _draft(service, customer.id), asset).id,
        actor_id=user.id,
    )
    variant = session.get(CatalogVariant, asset.variant_id)
    assert variant is not None
    login = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    products = client.get(
        "/api/operations/catalog/products?product_filter=rental",
        headers=headers,
    )
    product = client.get(
        f"/api/operations/catalog/products/{variant.product_id}",
        headers=headers,
    )
    rented = client.get(
        "/api/operations/rental/assets?asset_filter=rented",
        headers=headers,
    )

    assert products.status_code == 200
    assert products.json()[0]["variant_count"] == 1
    assert products.json()[0]["rental_asset_count"] == 1
    assert products.json()[0]["available_asset_count"] == 0
    assert products.json()[0]["needs_initial_price"] is True
    assert products.json()[0]["primary_image_id"] is None
    assert product.status_code == 200
    assert product.json()["rental_assets"][0]["asset_number"] == asset.asset_number
    assert rented.status_code == 200
    assert rented.json()[0]["current_order_id"] == str(order.id)
    assert rented.json()[0]["current_order_number"] == order.order_number

    session.add(
        Price(
            variant_id=variant.id,
            price_type=PriceType.RETAIL,
            amount=Decimal("1500"),
            currency="RUB",
            effective_from=NOW + timedelta(days=30),
        )
    )
    session.commit()
    needs_price = client.get(
        "/api/operations/catalog/products?product_filter=needs_price",
        headers=headers,
    )
    refreshed_product = client.get(
        f"/api/operations/catalog/products/{variant.product_id}",
        headers=headers,
    )

    assert needs_price.status_code == 200
    assert needs_price.json() == []
    assert refreshed_product.json()["variants"][0]["current_retail_price"] is None
    assert refreshed_product.json()["variants"][0]["has_ever_retail_price"] is True

    service.complete_items(
        order.id,
        RentalOrderItemsComplete(
            items=[
                RentalOrderItemCompletion(
                    item_id=order.items[0].id,
                    outcome=RentalOrderItemOutcome.LOST,
                    charged_amount=Decimal("0"),
                )
            ]
        ),
        actor_id=user.id,
    )
    lost = client.get(
        "/api/operations/rental/assets?asset_filter=lost",
        headers=headers,
    )

    assert lost.status_code == 200
    assert lost.json()[0]["is_lost"] is True
    assert lost.json()[0]["current_order_id"] is None


def test_rental_asset_passport_journals_and_customer_history(
    client: TestClient,
    session: Session,
    customer: Customer,
    asset: RentalAssetRecord,
    tmp_path: Path,
) -> None:
    """Lifecycle projections combine immutable orders with append-only service evidence."""
    user = IdentityService(session).create_admin(
        "passport@example.com",
        "Passport Operator",
        "long enough password",
    )
    service = RentalOrderService(session)
    order = service.issue(
        _add_asset(service, _draft(service, customer.id), asset).id,
        actor_id=user.id,
    )
    item = order.items[0]
    login = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.app.dependency_overrides[get_lifecycle_image_service] = lambda: ImageService(
        session,
        storage=LocalImageStorage(tmp_path / "storage"),
    )

    maintenance = client.post(
        f"/api/operations/rental/assets/{asset.id}/maintenance",
        headers=headers,
        json={
            "service_type": "cleaning",
            "result": "Ready for use",
            "comment": "Routine cleaning",
            "cost": "50",
        },
    )
    damage = client.post(
        f"/api/operations/rental/assets/{asset.id}/damages",
        headers=headers,
        json={
            "order_item_id": str(item.id),
            "description": "Scratched housing",
            "severity": "minor",
        },
    )
    photo_bytes = BytesIO()
    PillowImage.new("RGB", (8, 6), color="green").save(photo_bytes, format="PNG")
    photo = client.post(
        f"/api/operations/rental/assets/{asset.id}/condition-photos",
        headers=headers,
        data={"stage": "after", "order_item_id": str(item.id)},
        files={"file": ("after.png", photo_bytes.getvalue(), "image/png")},
    )
    completed = service.complete_items(
        order.id,
        RentalOrderItemsComplete(
            items=[
                RentalOrderItemCompletion(
                    item_id=item.id,
                    outcome=RentalOrderItemOutcome.RETURNED,
                    condition=AssetCondition.GOOD,
                    charged_amount=Decimal("700"),
                )
            ]
        ),
        actor_id=user.id,
    )
    passport = client.get(
        f"/api/operations/rental/assets/{asset.id}/passport",
        headers=headers,
    )
    customer_history = client.get(
        f"/api/operations/rental/customers/{customer.id}/history",
        headers=headers,
    )

    assert maintenance.status_code == 201
    assert maintenance.json()["performer_name"] == user.full_name
    assert damage.status_code == 201
    assert damage.json()["order_number"] == order.order_number
    assert photo.status_code == 201
    assert photo.json()["stage"] == "after"
    assert completed.status is RentalOrderStatus.CLOSED
    assert passport.status_code == 200
    body = passport.json()
    assert body["completed_rental_count"] == 1
    assert {event["event_type"] for event in body["timeline"]} == {
        "intake",
        "issued",
        "maintenance",
        "damage",
        "returned",
    }
    assert [event["occurred_at"] for event in body["timeline"]] == sorted(
        event["occurred_at"] for event in body["timeline"]
    )
    assert body["maintenance"][0]["service_type"] == "cleaning"
    assert body["maintenance"][0]["cost"] == "50.00"
    assert body["economics"]["acquisition_cost"] == "500.00"
    assert body["economics"]["revenue"] == "700.00"
    assert body["economics"]["expenses"] == "50.00"
    assert body["economics"]["net_income"] == "650.00"
    assert body["economics"]["rental_count"] == 1
    assert "paid_back" in body["economics"]["flags"]
    assert body["damages"][0]["description"] == "Scratched housing"
    assert body["condition_photos"][0]["stage"] == "after"
    assert customer_history.status_code == 200
    assert customer_history.json()["rental_count"] == 1
    assert customer_history.json()["active_rentals"] == []
    assert customer_history.json()["completed_rentals"][0]["order_id"] == str(order.id)


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

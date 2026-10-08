from __future__ import annotations

import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import core.sales.service as sales_service_module
from core.catalog.deletion import CatalogHardDeleteService
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.inventory.models import StockMovement
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.sales.enums import SaleItemSource, SaleStatus
from core.sales.models import Sale, SaleItem
from core.sales.routes import _sellable_balances
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an isolated database with the complete application metadata."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def users(session: Session) -> tuple[User, User]:
    """Create two operators used by Sales ownership checks."""
    first = User(email="sales-1@example.com", full_name="First", password_hash="unused")
    second = User(email="sales-2@example.com", full_name="Second", password_hash="unused")
    session.add_all([first, second])
    session.commit()
    return first, second


@pytest.fixture
def current_user(users: tuple[User, User]) -> dict[str, User]:
    """Expose a mutable authenticated operator for cross-owner API tests."""
    return {"value": users[0]}


@pytest.fixture
def client(session: Session, current_user: dict[str, User]) -> Generator[TestClient]:
    """Provide a Sales API client backed by the isolated database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: current_user["value"]
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def priced_variant(session: Session) -> CatalogVariant:
    """Create one operational Variant with a current retail price."""
    category = Category(title="Stationery", slug="stationery")
    product = CatalogProduct(title="Цветной картон", slug="colored-cardboard", category=category)
    variant = CatalogVariant(
        product=product,
        title="A4",
        sku="SKU-SALE-001",
        barcode="2000000000015",
        attributes={},
    )
    session.add(variant)
    session.flush()
    session.add(
        Price(
            variant_id=variant.id,
            price_type=PriceType.RETAIL,
            amount=Decimal("500.00"),
            currency="RUB",
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()
    return variant


def test_checkout_context_balance_query_returns_variant_mapping(
    session: Session, priced_variant: CatalogVariant
) -> None:
    """Checkout stock preflight consumes SQLAlchemy rows instead of the Result object."""
    assert _sellable_balances(session, [priced_variant.id]) == {
        priced_variant.id: Decimal("0")
    }


def _create_sale(client: TestClient) -> dict[str, object]:
    """Create one draft through the public API."""
    response = client.post("/api/sales")
    assert response.status_code == 201
    return response.json()


def _add_variant(client: TestClient, sale_id: str, variant_id: object) -> dict[str, object]:
    """Add one Variant through the public API and return the Sale."""
    response = client.post(
        f"/api/sales/{sale_id}/items/by-variant",
        json={"variant_id": str(variant_id)},
    )
    assert response.status_code == 200
    return response.json()


def test_multiple_draft_sales_are_persistent_and_listed(client: TestClient) -> None:
    """One operator may keep several independent draft carts."""
    first = _create_sale(client)
    second = _create_sale(client)

    listed = client.get("/api/sales")
    reloaded = client.get(f"/api/sales/{first['id']}")

    assert listed.status_code == 200
    assert {sale["id"] for sale in listed.json()} == {first["id"], second["id"]}
    assert reloaded.json()["status"] == SaleStatus.DRAFT


def test_operators_cannot_read_or_mutate_each_others_sales(
    client: TestClient,
    current_user: dict[str, User],
    users: tuple[User, User],
    priced_variant: CatalogVariant,
) -> None:
    """Sale ownership is enforced for both reads and commands."""
    sale = _create_sale(client)
    current_user["value"] = users[1]

    read = client.get(f"/api/sales/{sale['id']}")
    mutate = client.post(
        f"/api/sales/{sale['id']}/items/by-variant",
        json={"variant_id": str(priced_variant.id)},
    )

    assert read.status_code == 404
    assert mutate.status_code == 404


def test_auto_catalog_add_creates_sale_and_repeated_add_increments(
    client: TestClient,
    priced_variant: CatalogVariant,
) -> None:
    """Catalog add creates the first cart and reuses one Variant line thereafter."""
    created = client.post(
        "/api/sales/auto/items/by-variant",
        json={"variant_id": str(priced_variant.id)},
    )
    sale = created.json()
    repeated = _add_variant(client, sale["id"], priced_variant.id)

    assert created.status_code == 201
    assert repeated["line_count"] == 1
    assert repeated["item_quantity"] == 2
    assert repeated["items"][0]["quantity"] == 2
    assert repeated["total_amount"] == "1000.00"


def test_auto_scan_resolves_exact_barcode_and_unknown_scan_creates_nothing(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """Sales resolves only canonical barcodes and rejects unknown values cleanly."""
    unknown = client.post(
        "/api/sales/auto/items/by-barcode",
        json={"barcode": "UNKNOWN-SALE-BARCODE"},
    )
    created = client.post(
        "/api/sales/auto/items/by-barcode",
        json={"barcode": priced_variant.barcode},
    )
    repeated = client.post(
        f"/api/sales/{created.json()['id']}/items/by-barcode",
        json={"barcode": priced_variant.barcode},
    )

    assert unknown.status_code == 404
    assert "не найден" in unknown.json()["detail"]
    assert created.status_code == 201
    assert repeated.json()["items"][0]["quantity"] == 2
    assert session.scalar(select(Sale).where(Sale.id == UUID(created.json()["id"]))) is not None
    assert session.query(Sale).count() == 1


def test_missing_price_refuses_add_without_creating_zero_sale(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """A Variant without a current retail price never enters a cart as zero."""
    other = CatalogVariant(
        product_id=priced_variant.product_id,
        title="Без цены",
        sku="SKU-SALE-NO-PRICE",
        barcode="NO-PRICE-BARCODE",
        attributes={},
    )
    session.add(other)
    session.commit()

    response = client.post(
        "/api/sales/auto/items/by-variant",
        json={"variant_id": str(other.id)},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "У товара не указана цена"
    assert session.query(Sale).count() == 0


def test_sale_item_snapshots_survive_later_catalog_and_price_changes(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """Historical cart display and totals use captured facts rather than live Catalog data."""
    sale = _create_sale(client)
    added = _add_variant(client, sale["id"], priced_variant.id)
    priced_variant.product.title = "Новое название"
    priced_variant.title = "Новый вариант"
    session.add(
        Price(
            variant_id=priced_variant.id,
            price_type=PriceType.RETAIL,
            amount=Decimal("900.00"),
            currency="RUB",
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()

    reloaded = client.get(f"/api/sales/{sale['id']}").json()
    item = reloaded["items"][0]

    assert item["product_title_snapshot"] == "Цветной картон"
    assert item["variant_title_snapshot"] == "A4"
    assert item["display_label_snapshot"] == "Цветной картон · A4"
    assert item["sku_snapshot"] == "SKU-SALE-001"
    assert item["barcode_snapshot"] == "2000000000015"
    assert item["unit_price"] == "500.00"
    assert reloaded["total_amount"] == added["total_amount"] == "500.00"


def test_quantity_commands_remove_at_zero_and_totals_remain_decimal(
    client: TestClient,
    priced_variant: CatalogVariant,
) -> None:
    """Relative commands recalculate totals and remove a line at zero."""
    sale = _create_sale(client)
    added = _add_variant(client, sale["id"], priced_variant.id)
    item_id = added["items"][0]["id"]

    increased = client.post(
        f"/api/sales/{sale['id']}/items/{item_id}/quantity", json={"delta": 1}
    ).json()
    removed = client.post(
        f"/api/sales/{sale['id']}/items/{item_id}/quantity", json={"delta": -1}
    ).json()
    removed = client.post(
        f"/api/sales/{sale['id']}/items/{item_id}/quantity", json={"delta": -1}
    ).json()

    assert increased["items"][0]["line_total"] == "1000.00"
    assert removed["items"] == []
    assert removed["total_amount"] == "0.00"


def test_percentage_discount_presets_and_custom_value_are_exact(
    client: TestClient, priced_variant: CatalogVariant
) -> None:
    """No discount, presets and custom Decimal percentages persist exact money facts."""
    sale = _create_sale(client)
    unchanged = _add_variant(client, sale["id"], priced_variant.id)
    assert unchanged["subtotal_amount"] == unchanged["total_amount"] == "500.00"

    five = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "5.00"}
    ).json()
    ten = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "10.00"}
    ).json()
    custom = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "7.50"}
    ).json()

    assert (five["discount_amount"], five["total_amount"]) == ("25.00", "475.00")
    assert (ten["discount_amount"], ten["total_amount"]) == ("50.00", "450.00")
    assert custom["discount_value"] == "7.50"
    assert custom["discount_amount"] == "37.50"
    assert custom["total_amount"] == "462.50"


def test_invalid_or_fiscally_unsafe_discount_is_rejected(
    client: TestClient, priced_variant: CatalogVariant
) -> None:
    """Server validation rejects negative, excessive and zero-price fiscal allocations."""
    sale = _create_sale(client)
    _add_variant(client, sale["id"], priced_variant.id)
    negative = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "-1"}
    )
    excessive = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "100"}
    )

    assert negative.status_code == excessive.status_code == 422


def test_manual_item_participates_in_totals_and_discount_without_variant(
    client: TestClient,
) -> None:
    """An open item is a normal commercial line but has no Catalog stock identity."""
    sale = _create_sale(client)
    added = client.post(
        f"/api/sales/{sale['id']}/items/manual",
        json={"name": "Игрушка антистресс", "unit_price": "350.00", "quantity": 2},
    )
    discounted = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "10"}
    )

    assert added.status_code == 200
    item = added.json()["items"][0]
    assert item["source"] == SaleItemSource.MANUAL
    assert item["variant_id"] is None
    assert item["sku_snapshot"] is None
    assert item["line_total"] == "700.00"
    assert discounted.json()["subtotal_amount"] == "700.00"
    assert discounted.json()["discount_amount"] == "70.00"
    assert discounted.json()["total_amount"] == "630.00"
    assert discounted.json()["items"][0]["fiscal_line_total"] == "630.00"


def test_manual_item_requires_name_and_price(client: TestClient) -> None:
    """The open-item API rejects blank names and missing prices server-side."""
    sale = _create_sale(client)
    blank = client.post(
        f"/api/sales/{sale['id']}/items/manual",
        json={"name": "   ", "unit_price": "10.00", "quantity": 1},
    )
    missing = client.post(
        f"/api/sales/{sale['id']}/items/manual",
        json={"name": "Разовая услуга", "quantity": 1},
    )

    assert blank.status_code == missing.status_code == 422


def test_discount_allocation_rounding_is_deterministic_and_exact(client: TestClient) -> None:
    """Largest-remainder allocation reconciles fiscal lines to the Sale total."""
    sale = _create_sale(client)
    for name, price in (("Строка A", "3.33"), ("Строка B", "6.67")):
        response = client.post(
            f"/api/sales/{sale['id']}/items/manual",
            json={"name": name, "unit_price": price, "quantity": 1},
        )
        assert response.status_code == 200
    first = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "10"}
    ).json()
    second = client.patch(
        f"/api/sales/{sale['id']}/discount", json={"discount_value": "10"}
    ).json()

    assert first["total_amount"] == "9.00"
    assert [item["fiscal_line_total"] for item in first["items"]] == ["3.00", "6.00"]
    assert [item["fiscal_line_total"] for item in second["items"]] == ["3.00", "6.00"]
    assert sum(Decimal(item["fiscal_line_total"]) for item in first["items"]) == Decimal(
        first["total_amount"]
    )


def test_cancelled_sale_is_historical_and_rejects_new_items(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """Cancellation preserves the Sale while removing it from mutable draft targets."""
    sale = _create_sale(client)
    _add_variant(client, sale["id"], priced_variant.id)

    cancelled = client.post(f"/api/sales/{sale['id']}/cancel")
    rejected = client.post(
        f"/api/sales/{sale['id']}/items/by-variant",
        json={"variant_id": str(priced_variant.id)},
    )

    assert cancelled.json()["status"] == SaleStatus.CANCELLED
    assert rejected.status_code == 409
    assert client.get("/api/sales").json() == []
    assert client.get(f"/api/sales/{sale['id']}").status_code == 200
    assert session.query(SaleItem).count() == 1


def test_draft_sales_do_not_create_inventory_movements(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """Cart creation, edits, and cancellation have no Inventory ledger effect."""
    sale = _create_sale(client)
    added = _add_variant(client, sale["id"], priced_variant.id)
    item_id = added["items"][0]["id"]
    client.post(f"/api/sales/{sale['id']}/items/{item_id}/quantity", json={"delta": 1})
    client.post(f"/api/sales/{sale['id']}/cancel")

    assert session.query(StockMovement).count() == 0


def test_sale_history_blocks_catalog_hard_delete(
    client: TestClient,
    session: Session,
    priced_variant: CatalogVariant,
) -> None:
    """Catalog preflight reports SaleItem snapshots as protected history."""
    sale = _create_sale(client)
    _add_variant(client, sale["id"], priced_variant.id)

    preflight = CatalogHardDeleteService(session).preflight_variant(priced_variant.id)

    assert preflight.can_delete is False
    assert {blocker.code for blocker in preflight.blockers} == {"sale_items"}


def test_explicit_remove_command_recalculates_empty_cart(
    client: TestClient,
    priced_variant: CatalogVariant,
) -> None:
    """The remove-line command deletes only the line and retains the draft Sale."""
    sale = _create_sale(client)
    added = _add_variant(client, sale["id"], priced_variant.id)

    response = client.delete(
        f"/api/sales/{sale['id']}/items/{added['items'][0]['id']}"
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total_amount"] == "0.00"


def test_sales_domain_has_no_provider_transport_dependency() -> None:
    """Sprint 5.1 Sales remains independent from AQSI and integration transports."""
    source = inspect.getsource(sales_service_module)

    assert "core.integrations" not in source
    assert "aqsi" not in source.casefold()

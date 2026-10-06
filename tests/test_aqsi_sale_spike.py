from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import (
    CatalogProduct,
    CatalogVariant,
    CatalogVariantBarcode,
    Category,
)
from core.config import Settings, get_settings
from core.database import get_session
from core.dev.aqsi_sale_spike import (
    _direct_attempts,
    _submitted_references,
    get_spike_gateway,
)
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.integrations.models import Integration, IntegrationCredential
from core.inventory.models import StockMovement
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import Base


class FakePendingOrderGateway:
    """Record AQSI calls without contacting or mutating the real account."""

    def __init__(self) -> None:
        """Create an empty fake with an optional configured failure."""
        self.created: list[dict[str, object]] = []
        self.purchases: list[dict[str, object]] = []
        self.purchase_calls = 0
        self.receipts: list[dict[str, object]] = []
        self.receipt_calls = 0
        self.status: dict[str, object] = {"status": "Отложен", "receipts": []}
        self.error: AqsiApiError | None = None
        self.purchase_error: AqsiApiError | None = None
        self.operation_error: AqsiApiError | None = None
        self.receipt_error: AqsiApiError | None = None
        self.operations: dict[str, dict[str, object]] = {
            "purchase-operation": {
                "operationId": "purchase-operation",
                "status": "Pending",
                "type": "acquiring.purchase",
            },
            "receipt-operation": {
                "operationId": "receipt-operation",
                "status": "Pending",
                "type": "receipt.process",
            },
        }

    def create_pending_order(self, payload: dict[str, object]) -> dict[str, object]:
        """Record one outbound order or raise the configured error."""
        if self.error:
            raise self.error
        self.created.append(payload)
        return {"guid": "aqsi-order-guid"}

    def get_pending_order(self, order_id: str) -> dict[str, object]:
        """Return the configured read projection without creating an order."""
        del order_id
        if self.error:
            raise self.error
        return self.status

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Record one acquiring request without touching a physical device."""
        self.purchase_calls += 1
        if self.purchase_error:
            raise self.purchase_error
        self.purchases.append(payload)
        return {"operationId": "purchase-operation"}

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Return the configured state for one fake device operation."""
        if self.operation_error:
            raise self.operation_error
        return self.operations[operation_id]

    def create_receipt(self, payload: dict[str, object]) -> dict[str, object]:
        """Record one receipt request without fiscalizing it."""
        self.receipt_calls += 1
        if self.receipt_error:
            raise self.receipt_error
        self.receipts.append(payload)
        return {"operationId": "receipt-operation"}


@pytest.fixture(autouse=True)
def clear_duplicate_guard() -> Generator[None]:
    """Keep the process-local duplicate guard isolated between tests."""
    _submitted_references.clear()
    _direct_attempts.clear()
    yield
    _submitted_references.clear()
    _direct_attempts.clear()


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the catalog and immutable pricing facts used by the spike."""
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
            Price.__table__,
            StockMovement.__table__,
            Integration.__table__,
            IntegrationCredential.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def variants(session: Session) -> list[CatalogVariant]:
    """Create two real Core variants with current retail prices."""
    category = Category(title="Spike", slug="spike")
    product = CatalogProduct(title="Test product", slug="test-product", category=category)
    variants = [
        CatalogVariant(
            product=product,
            title="Red",
            sku="SKU-900001",
            barcode="2000009000010",
        ),
        CatalogVariant(
            product=product,
            title="Blue",
            sku="SKU-900002",
            barcode="2000009000027",
        ),
    ]
    session.add_all(variants)
    session.flush()
    session.add_all(
        [
            Price(
                variant_id=variants[0].id,
                price_type=PriceType.RETAIL,
                amount=Decimal("300.00"),
                currency="RUB",
                effective_from=datetime.now(UTC),
            ),
            Price(
                variant_id=variants[1].id,
                price_type=PriceType.RETAIL,
                amount=Decimal("450.00"),
                currency="RUB",
                effective_from=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    return variants


def make_settings(*, enabled: bool = True) -> Settings:
    """Build explicit isolated settings without reading real AQSI credentials."""
    return Settings(
        jwt_secret="test-secret",
        aqsi_api_key=SecretStr("test-api-key"),
        aqsi_sale_spike_enabled=enabled,
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
    )


@pytest.fixture
def gateway() -> FakePendingOrderGateway:
    return FakePendingOrderGateway()


@pytest.fixture
def client(session: Session, gateway: FakePendingOrderGateway) -> Generator[TestClient]:
    """Provide an admin-authorized spike client with a fake AQSI boundary."""
    app = create_app()
    admin = User(
        email="spike-admin@example.com",
        full_name="Spike Admin",
        password_hash="unused",
        is_admin=True,
    )

    def override_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_settings] = make_settings
    app.dependency_overrides[get_spike_gateway] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def order_body(variants: list[CatalogVariant], request_id: str | None = None) -> dict[str, Any]:
    return {
        "request_id": request_id or str(uuid4()),
        "confirm_real_operation": True,
        "lines": [
            {"variant_id": str(variants[0].id), "quantity": 2, "test_price": "10.15"},
            {"variant_id": str(variants[1].id), "quantity": 3, "test_price": "7.25"},
        ],
    }


def test_spike_maps_distinct_real_variants_and_preserves_core_facts(
    client: TestClient,
    session: Session,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    price_count = session.query(Price).count()
    movement_count = session.query(StockMovement).count()

    response = client.post("/api/dev/aqsi-sale-spike/orders", json=order_body(variants))

    assert response.status_code == 201
    assert response.json()["expected_total"] == "42.05"
    assert response.json()["device_id"] == "748887"
    assert len(gateway.created) == 1
    payload = gateway.created[0]
    assert payload["device"] == "748887"
    content = payload["content"]
    assert isinstance(content, dict)
    positions = content["positions"]
    assert isinstance(positions, list)
    mapped_lines = [
        (position["sku"], position["quantity"], position["price"])
        for position in positions
    ]
    assert mapped_lines == [
        ("SKU-900001", 2, 10.15),
        ("SKU-900002", 3, 7.25),
    ]
    assert all(position["tax"] == 6 for position in positions)
    assert all(position["paymentMethodType"] == 4 for position in positions)
    assert all(position["paymentSubjectType"] == 1 for position in positions)
    assert session.query(Price).count() == price_count
    assert session.query(StockMovement).count() == movement_count
    assert [price.amount for price in session.query(Price).order_by(Price.amount)] == [
        Decimal("300.00"),
        Decimal("450.00"),
    ]


def test_spike_rejects_repeated_submission_without_second_aqsi_call(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    body = order_body(variants, str(uuid4()))
    assert client.post("/api/dev/aqsi-sale-spike/orders", json=body).status_code == 201

    repeated = client.post("/api/dev/aqsi-sale-spike/orders", json=body)

    assert repeated.status_code == 409
    assert len(gateway.created) == 1


def test_spike_surfaces_aqsi_failure_without_success_result(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    gateway.error = AqsiApiError(
        "timeout",
        "AQSI request timed out.",
        retryable=True,
        outcome_unknown=True,
    )
    body = order_body(variants)

    response = client.post("/api/dev/aqsi-sale-spike/orders", json=body)

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "timeout",
        "message": "AQSI request timed out.",
        "retryable": False,
        "core_reference": response.json()["detail"]["core_reference"],
        "outcome_unknown": True,
    }
    repeated = client.post("/api/dev/aqsi-sale-spike/orders", json=body)
    assert repeated.status_code == 409


def test_spike_status_reads_existing_order_without_creating_one(
    client: TestClient,
    gateway: FakePendingOrderGateway,
) -> None:
    response = client.get("/api/dev/aqsi-sale-spike/orders/CORE-SPIKE-existing")

    assert response.status_code == 200
    assert response.json() == gateway.status
    assert gateway.created == []


def test_spike_requires_admin_and_feature_flag(
    client: TestClient,
    variants: list[CatalogVariant],
) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: User(
        email="employee@example.com",
        full_name="Employee",
        password_hash="unused",
        is_admin=False,
    )
    assert client.post(
        "/api/dev/aqsi-sale-spike/orders", json=order_body(variants)
    ).status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: User(
        email="admin@example.com",
        full_name="Admin",
        password_hash="unused",
        is_admin=True,
    )
    client.app.dependency_overrides[get_settings] = lambda: make_settings(enabled=False)
    assert client.post(
        "/api/dev/aqsi-sale-spike/orders", json=order_body(variants)
    ).status_code == 503


def completed_purchase(amount: int = 4205) -> dict[str, object]:
    slip = {
        "id": "slip-physical-payment",
        "content": {
            "type": "purchase",
            "amount": amount,
            "dateTime": "2026-09-23T12:00:00+03:00",
            "sequenceNumber": "0001",
        },
    }
    return {
        "operationId": "purchase-operation",
        "status": "Completed",
        "type": "acquiring.purchase",
        "result": json.dumps(slip),
    }


def test_direct_purchase_uses_exact_total_device_and_single_submission(
    client: TestClient,
    session: Session,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    price_count = session.query(Price).count()
    movement_count = session.query(StockMovement).count()
    body = order_body(variants)

    response = client.post("/api/dev/aqsi-sale-spike/direct", json=body)

    assert response.status_code == 201
    assert response.json()["state"] == "payment_in_progress"
    assert response.json()["expected_total"] == "42.05"
    assert gateway.purchases == [
        {
            "deviceId": 748887,
            "ttlMillis": 120000,
            "amount": 4205,
            "mode": "card_only",
            "printCount": 0,
        }
    ]
    assert client.post("/api/dev/aqsi-sale-spike/direct", json=body).status_code == 409
    assert len(gateway.purchases) == 1
    assert session.query(Price).count() == price_count
    assert session.query(StockMovement).count() == movement_count


def test_direct_pending_or_failed_purchase_never_creates_receipt(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    started = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants)).json()
    path = f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"

    pending = client.post(path)
    assert pending.json()["state"] == "payment_in_progress"
    assert gateway.receipts == []

    gateway.operations["purchase-operation"] = {
        "operationId": "purchase-operation",
        "status": "Error",
        "type": "acquiring.purchase",
        "message": "Карта отклонена",
    }
    failed = client.post(path)
    assert failed.json()["state"] == "payment_failed"
    assert failed.json()["aqsi_problem"] == "Карта отклонена"
    assert gateway.receipts == []


def test_direct_completed_purchase_fiscalizes_exact_basket_snapshot(
    client: TestClient,
    session: Session,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    started = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants)).json()
    session.add(
        Price(
            variant_id=variants[0].id,
            price_type=PriceType.RETAIL,
            amount=Decimal("9999.00"),
            currency="RUB",
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()
    gateway.operations["purchase-operation"] = completed_purchase()

    response = client.post(
        f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"
    )

    assert response.status_code == 200
    assert response.json()["state"] == "payment_success_fiscalization_pending"
    assert response.json()["slip_reference"] == "slip-physical-payment"
    assert len(gateway.receipts) == 1
    receipt = gateway.receipts[0]
    assert receipt["deviceId"] == 748887
    assert receipt["info"] == {"taxSystemCode": 2}
    assert receipt["ignoreItemCodeCheck"] is False
    assert receipt["skipPrinting"] is False
    positions = receipt["positions"]
    assert isinstance(positions, list)
    mapped_positions = [
        (position["info"]["baseQuantity"], position["info"]["finalPrice"])
        for position in positions
    ]
    assert mapped_positions == [
        ("2", 1015),
        ("3", 725),
    ]
    assert [position["externalId"] for position in positions] == [
        str(variants[0].id),
        str(variants[1].id),
    ]
    payments = receipt["payments"]
    assert isinstance(payments, list)
    assert payments[0]["type"] == 1
    assert payments[0]["amount"] == 4205
    assert payments[0]["slip"]["id"] == "slip-physical-payment"
    assert sum(
        int(position["info"]["baseQuantity"]) * position["info"]["finalPrice"]
        for position in positions
    ) == payments[0]["amount"]

    repeated = client.post(
        f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"
    )
    assert repeated.status_code == 200
    assert len(gateway.purchases) == 1
    assert len(gateway.receipts) == 1
    gateway.operations["receipt-operation"] = {
        "operationId": "receipt-operation",
        "status": "Completed",
        "type": "receipt.process",
    }
    completed = client.post(
        f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"
    )
    assert completed.json()["state"] == "completed"


def test_direct_receipt_failure_is_explicit_partial_failure(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    started = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants)).json()
    gateway.operations["purchase-operation"] = completed_purchase()
    path = f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"
    assert client.post(path).json()["state"] == "payment_success_fiscalization_pending"
    gateway.operations["receipt-operation"] = {
        "operationId": "receipt-operation",
        "status": "Error",
        "type": "receipt.process",
        "message": "Смена закрыта",
    }

    failed = client.post(path)

    assert failed.json()["state"] == "payment_success_fiscalization_failed"
    assert "ОПЛАТА ПРОШЛА" in failed.json()["message"]
    assert failed.json()["purchase_operation_id"] == "purchase-operation"
    assert failed.json()["receipt_operation_id"] == "receipt-operation"
    assert failed.json()["aqsi_problem"] == "Смена закрыта"


def test_direct_unknown_receipt_submission_is_never_retried(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    started = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants)).json()
    gateway.operations["purchase-operation"] = completed_purchase()
    gateway.receipt_error = AqsiApiError(
        "timeout",
        "AQSI request timed out.",
        retryable=True,
        outcome_unknown=True,
    )
    path = f"/api/dev/aqsi-sale-spike/direct/{started['core_reference']}/progress"

    unknown = client.post(path)
    repeated = client.post(path)

    assert unknown.json()["state"] == "payment_success_fiscalization_unknown"
    assert repeated.json()["state"] == "payment_success_fiscalization_unknown"
    assert gateway.receipts == []
    assert gateway.receipt_calls == 1
    assert len(gateway.purchases) == 1


def test_direct_unknown_purchase_outcome_is_not_retried(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    gateway.purchase_error = AqsiApiError(
        "timeout",
        "AQSI request timed out.",
        retryable=True,
        outcome_unknown=True,
    )
    body = order_body(variants)

    response = client.post("/api/dev/aqsi-sale-spike/direct", json=body)

    assert response.status_code == 201
    assert response.json()["state"] == "unknown"
    assert client.post("/api/dev/aqsi-sale-spike/direct", json=body).status_code == 409
    assert gateway.purchases == []
    assert gateway.purchase_calls == 1
    assert gateway.receipts == []


def test_direct_connect_timeout_is_known_not_started_and_allows_new_attempt(
    client: TestClient,
    variants: list[CatalogVariant],
    gateway: FakePendingOrderGateway,
) -> None:
    gateway.purchase_error = AqsiApiError(
        "connect_timeout",
        "Could not connect to AQSI before the request was sent.",
        retryable=True,
    )

    response = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants))

    assert response.status_code == 201
    assert response.json()["state"] == "payment_failed"
    assert "Оплата не запущена" in response.json()["message"]
    assert response.json()["purchase_operation_id"] is None
    assert gateway.purchase_calls == 1


def test_direct_requires_explicit_tax_system_configuration(
    client: TestClient,
    variants: list[CatalogVariant],
) -> None:
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        jwt_secret="test-secret",
        aqsi_api_key=SecretStr("test-api-key"),
        aqsi_sale_spike_enabled=True,
        aqsi_sale_spike_device_id="748887",
        _env_file=None,
    )

    response = client.post("/api/dev/aqsi-sale-spike/direct", json=order_body(variants))

    assert response.status_code == 503
    assert "tax system" in response.json()["detail"]


def test_aqsi_http_client_uses_official_deferred_order_paths() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/Slips/process/purchase"):
            return httpx2.Response(200, json={"operationId": "purchase-operation"})
        if request.url.path.endswith("/Receipts/process"):
            return httpx2.Response(200, json={"operationId": "receipt-operation"})
        if "/Operations/" in request.url.path:
            return httpx2.Response(
                200,
                json={"operationId": "purchase-operation", "status": "Pending"},
            )
        return httpx2.Response(200, json={"guid": "guid", "status": "Отложен"})

    settings = make_settings()
    with AqsiHttpClient(settings, transport=httpx2.MockTransport(handler)) as aqsi:
        assert aqsi.create_pending_order({"id": "CORE-SPIKE-one"})["guid"] == "guid"
        assert aqsi.get_pending_order("CORE-SPIKE-one")["status"] == "Отложен"
        assert aqsi.create_purchase({"deviceId": 748887, "amount": 5000}) == {
            "operationId": "purchase-operation"
        }
        assert aqsi.get_operation("purchase-operation")["status"] == "Pending"
        assert aqsi.create_receipt({"deviceId": 748887}) == {
            "operationId": "receipt-operation"
        }

    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/pub/v2/Orders/simple"),
        ("GET", "/pub/v2/Orders/simple/CORE-SPIKE-one"),
        ("POST", "/pub/v4/Slips/process/purchase"),
        ("GET", "/pub/v4/Operations/purchase-operation"),
        ("POST", "/pub/v4/Receipts/process"),
    ]


def test_aqsi_http_client_marks_connect_timeout_as_known_not_sent() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("connect failed", request=request)

    with (
        AqsiHttpClient(
            make_settings(), transport=httpx2.MockTransport(handler)
        ) as aqsi,
        pytest.raises(AqsiApiError) as raised,
    ):
        aqsi.create_purchase({"deviceId": 748887, "amount": 3000})

    assert raised.value.code == "connect_timeout"
    assert raised.value.retryable is True
    assert raised.value.outcome_unknown is False

from __future__ import annotations

import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.config import Settings, get_settings
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationOperation,
    PublicationStatus,
)
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.integrations.aqsi.payload import AqsiPayloadBuilder, AqsiVariantNotReadyError
from core.integrations.aqsi.processor import AqsiPublicationProcessor
from core.integrations.aqsi.routes import get_aqsi_queue
from core.integrations.aqsi.schemas import (
    AqsiDefaultCategoryPayload,
    AqsiGoodsPayload,
    AqsiShopPricePayload,
)
from core.integrations.aqsi.service import AqsiPublicationService
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import Base


def test_aqsi_worker_entrypoint_registers_user_table_in_fresh_process() -> None:
    """The standalone RQ worker must resolve BaseModel audit foreign keys."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import core.integrations.aqsi.jobs; "
                "from core.shared.db import Base; "
                "assert 'users' in Base.metadata.tables"
            ),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


class FakeAqsiGateway:
    """In-memory AQSI port used by deterministic integration tests."""

    def __init__(self) -> None:
        """Create an empty fake remote account."""
        self.categories: set[str] = set()
        self.goods: dict[str, dict[str, object]] = {}
        self.shop_ids = ["shop-1"]
        self.created_goods: list[dict[str, object]] = []
        self.updated_goods: list[dict[str, object]] = []

    def get_good(self, external_id: str) -> dict[str, object] | None:
        """Return a copied fake good when present."""
        good = self.goods.get(external_id)
        return dict(good) if good is not None else None

    def create_good(self, payload: AqsiGoodsPayload) -> None:
        """Create and immediately expose a fake good."""
        value = payload.as_aqsi_json()
        self.created_goods.append(value)
        self.goods[payload.id] = value

    def update_good(self, payload: AqsiGoodsPayload) -> None:
        """Update and immediately expose a fake good."""
        value = payload.as_aqsi_json()
        self.updated_goods.append(value)
        self.goods[payload.id] = value

    def category_exists(self, category_id: str) -> bool:
        """Return whether the fake category exists."""
        return category_id in self.categories

    def create_category(self, payload: AqsiDefaultCategoryPayload) -> None:
        """Create the fake default category immediately."""
        self.categories.add(payload.id)

    def list_shop_ids(self) -> list[str]:
        """Return fake active shops."""
        return list(self.shop_ids)

    def set_shop_price(self, payload: AqsiShopPricePayload) -> None:
        """Bind a fake good to its selected shop."""
        good = self.goods[payload.id]
        shop = payload.shops[0]
        good["price"] = payload.default_price
        good["shops"] = [{"id": shop["id"], "deletedAt": None}]


class FailingAqsiGateway(FakeAqsiGateway):
    """Fake gateway that rejects goods creation without exposing secrets."""

    def create_good(self, payload: AqsiGoodsPayload) -> None:
        """Raise one sanitized remote validation error."""
        del payload
        raise AqsiApiError("invalid_goods", "AQSI rejected the minimal payload.")


class CanonicalReadAqsiGateway(FakeAqsiGateway):
    """Mirror AQSI reads that normalize selected values after accepting a write."""

    def get_good(self, external_id: str) -> dict[str, object] | None:
        """Return numeric fiscal fields using AQSI read-side string encoding."""
        good = super().get_good(external_id)
        if good is None:
            return None
        for field in ("tax", "subject"):
            good[field] = str(good[field])
        good["unit"] = "шт."
        return good


class FakeQueue:
    """Record enqueued functions without running Redis or a worker."""

    def __init__(self) -> None:
        """Create an empty queue call history."""
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def enqueue(self, function: object, *args: object, **kwargs: object) -> None:
        """Record one enqueue request."""
        self.calls.append((function, args, kwargs))


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database containing AQSI dependencies."""
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
            Price.__table__,
            Publication.__table__,
            PublicationAttempt.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def aqsi_settings() -> Settings:
    """Provide enabled first-installation AQSI settings without real credentials."""
    return Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_enabled=True,
        aqsi_api_key=SecretStr("test-aqsi-key"),
        aqsi_tax_code=6,
        aqsi_verification_interval_seconds=0,
    )


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create one ready ordinary sellable Variant."""
    category = Category(title="Tools", slug="tools")
    product = CatalogProduct(title="Cordless drill", slug="cordless-drill", category=category)
    variant = CatalogVariant(
        product=product,
        title="Blue",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={"color": "blue"},
    )
    session.add(variant)
    session.flush()
    image = Image(
        source_key="images/source/drill.jpg",
        original_filename="drill.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=800,
        height=600,
        checksum="sha256:aqsi-drill",
    )
    image.links.append(
        ImageLink(
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=variant.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    session.add_all(
        [
            image,
            Price(
                variant_id=variant.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("249.90"),
                currency="RUB",
                effective_from=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    return variant


@pytest.fixture
def user(session: Session) -> User:
    """Create an authenticated publication actor."""
    return IdentityService(session).create_admin(
        "aqsi-admin@example.com",
        "AQSI Admin",
        "long enough password",
    )


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in and return the bearer header for an AQSI API request."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_payload_builder_emits_only_minimal_confirmed_aqsi_fields(
    session: Session,
    variant: CatalogVariant,
    aqsi_settings: Settings,
) -> None:
    """The first payload contains required fields and Core selling identifiers only."""
    builder = AqsiPayloadBuilder(session, aqsi_settings)

    payload = builder.build_goods(variant.id).as_aqsi_json()

    assert payload == {
        "id": str(variant.id),
        "group_id": "core-default-goods",
        "type": "simple",
        "name": "Cordless drill, Blue",
        "tax": 6,
        "unit": "Штука",
        "unitCode": 0,
        "subject": 1,
        "paymentMethodType": 4,
        "sku": "SKU-000001",
        "price": 249.9,
        "barcodes": ["2000000000015"],
    }
    assert "productionCost" not in payload
    assert "img" not in payload
    assert "markingType" not in payload


def test_publication_request_is_attributed_and_idempotent_while_pending(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """Repeated commands for one current payload reuse the active attempt."""
    service = AqsiPublicationService(session, aqsi_settings)

    publication, attempt, should_enqueue = service.request_publication(
        variant.id,
        actor_id=user.id,
    )
    duplicate_publication, duplicate_attempt, duplicate_enqueue = service.request_publication(
        variant.id,
        actor_id=user.id,
    )

    assert should_enqueue is True
    assert duplicate_enqueue is False
    assert duplicate_publication.id == publication.id
    assert duplicate_attempt.id == attempt.id
    assert attempt.created_by_id == user.id
    assert publication.external_id == str(variant.id)
    assert session.query(PublicationAttempt).count() == 1


def test_processor_creates_category_good_and_verifies_projection(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """A fake end-to-end publication reaches published only after remote verification."""
    service = AqsiPublicationService(session, aqsi_settings)
    publication, attempt, _ = service.request_publication(variant.id, actor_id=user.id)
    gateway = FakeAqsiGateway()

    AqsiPublicationProcessor(
        session,
        aqsi_settings,
        gateway,
        sleeper=lambda _: None,
    ).process(attempt.id)

    session.refresh(publication)
    session.refresh(attempt)
    assert gateway.categories == {"core-default-goods"}
    assert len(gateway.created_goods) == 1
    assert gateway.goods[str(variant.id)]["shops"] == [{"id": "shop-1", "deletedAt": None}]
    assert attempt.status is PublicationAttemptStatus.PUBLISHED
    assert attempt.operation is PublicationOperation.CREATE
    assert publication.status is PublicationStatus.PUBLISHED
    assert publication.last_verified_payload_hash == attempt.payload_hash
    assert publication.published_at is not None

    _, duplicate, should_enqueue = service.request_publication(variant.id, actor_id=user.id)
    assert should_enqueue is False
    assert duplicate.id == attempt.id


def test_changed_price_creates_update_attempt(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """Changed authoritative data is sent as an update under the same external ID."""
    service = AqsiPublicationService(session, aqsi_settings)
    publication, initial, _ = service.request_publication(variant.id, actor_id=user.id)
    gateway = FakeAqsiGateway()
    processor = AqsiPublicationProcessor(
        session,
        aqsi_settings,
        gateway,
        sleeper=lambda _: None,
    )
    processor.process(initial.id)
    session.add(
        Price(
            variant_id=variant.id,
            price_type=PriceType.RETAIL,
            amount=Decimal("299.00"),
            currency="RUB",
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()

    assert service.is_outdated(publication) is True

    same_publication, update, should_enqueue = service.request_publication(
        variant.id,
        actor_id=user.id,
    )
    processor.process(update.id)

    assert should_enqueue is True
    assert same_publication.id == publication.id
    assert update.attempt_number == 2
    assert update.operation is PublicationOperation.UPDATE
    assert gateway.updated_goods[-1]["price"] == 299.0
    assert service.is_outdated(publication) is False


def test_processor_accepts_aqsi_canonical_read_values(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """Verification accepts AQSI's string enums and canonical piece abbreviation."""
    service = AqsiPublicationService(session, aqsi_settings)
    publication, attempt, _ = service.request_publication(variant.id, actor_id=user.id)

    AqsiPublicationProcessor(
        session,
        aqsi_settings,
        CanonicalReadAqsiGateway(),
        sleeper=lambda _: None,
    ).process(attempt.id)

    session.refresh(publication)
    session.refresh(attempt)
    assert publication.status is PublicationStatus.PUBLISHED
    assert attempt.status is PublicationAttemptStatus.PUBLISHED


def test_multiple_shops_require_explicit_configuration(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """Core never guesses a cash-register shop when the account has several."""
    service = AqsiPublicationService(session, aqsi_settings)
    publication, attempt, _ = service.request_publication(variant.id, actor_id=user.id)
    gateway = FakeAqsiGateway()
    gateway.shop_ids = ["shop-1", "shop-2"]

    AqsiPublicationProcessor(
        session,
        aqsi_settings,
        gateway,
        sleeper=lambda _: None,
    ).process(attempt.id)

    session.refresh(publication)
    session.refresh(attempt)
    assert attempt.status is PublicationAttemptStatus.FAILED
    assert attempt.error_code == "shop_not_configured"
    assert gateway.created_goods == []


def test_remote_failure_is_recorded_without_changing_core_data(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """A definitive AQSI failure remains visible and does not alter the Variant."""
    service = AqsiPublicationService(session, aqsi_settings)
    publication, attempt, _ = service.request_publication(variant.id, actor_id=user.id)

    AqsiPublicationProcessor(
        session,
        aqsi_settings,
        FailingAqsiGateway(),
        sleeper=lambda _: None,
    ).process(attempt.id)

    session.refresh(publication)
    session.refresh(attempt)
    session.refresh(variant)
    assert attempt.status is PublicationAttemptStatus.FAILED
    assert attempt.error_code == "invalid_goods"
    assert publication.status is PublicationStatus.FAILED
    assert variant.is_active is True
    assert variant.barcode == "2000000000015"


def test_unready_variant_is_rejected_before_persistence(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """AQSI cannot bypass channel-independent Ready for Sale requirements."""
    session.query(Price).delete()
    session.commit()

    with pytest.raises(AqsiVariantNotReadyError) as exc_info:
        AqsiPublicationService(session, aqsi_settings).request_publication(
            variant.id,
            actor_id=user.id,
        )

    assert exc_info.value.missing_requirements == ["missing_retail_price"]
    assert session.query(Publication).count() == 0


def test_aqsi_publish_api_is_authenticated_and_queues_once(
    session: Session,
    variant: CatalogVariant,
    user: User,
    aqsi_settings: Settings,
) -> None:
    """The HTTP command returns 202, actor state and an idempotent queue result."""
    app = create_app()
    queue = FakeQueue()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_settings] = lambda: aqsi_settings
    app.dependency_overrides[get_aqsi_queue] = lambda: queue
    path = f"/api/publishing/aqsi/variants/{variant.id}"

    with TestClient(app) as client:
        assert client.post(path).status_code == 401
        headers = authorization_header(client, user)
        first = client.post(path, headers=headers)
        duplicate = client.post(path, headers=headers)
        status_response = client.get(path, headers=headers)
        history = client.get(f"{path}/attempts", headers=headers)

    assert first.status_code == 202
    assert first.json()["queued"] is True
    assert first.json()["attempt"]["created_by_id"] == str(user.id)
    assert duplicate.status_code == 202
    assert duplicate.json()["queued"] is False
    assert status_response.status_code == 200
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert len(queue.calls) == 1

    app.dependency_overrides.clear()


def test_http_client_uses_official_v2_paths_and_secret_header(
    aqsi_settings: Settings,
) -> None:
    """The real adapter maps the port to AQSI V2 without leaking its key."""
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path.endswith("/GoodsCategory/list"):
            return httpx2.Response(200, json=[])
        if request.url.path.endswith("/Shops/list"):
            return httpx2.Response(200, json=[{"id": "shop-1", "deletedAt": None}])
        if request.method == "GET":
            return httpx2.Response(400, json={"code": "not_found", "errors": ["missing"]})
        return httpx2.Response(200, json={"dateTime": "2026-07-18T12:00:00Z"})

    payload = AqsiGoodsPayload(
        id="variant-id",
        group_id="core-default-goods",
        name="Cordless drill, Blue",
        tax=6,
        sku="SKU-000001",
        price=249.9,
        barcodes=["2000000000015"],
    )
    category = AqsiDefaultCategoryPayload(
        id="core-default-goods",
        name="Товары Core",
        defaultTax=6,
    )

    with AqsiHttpClient(
        aqsi_settings,
        transport=httpx2.MockTransport(handler),
    ) as client:
        assert client.category_exists(category.id) is False
        client.create_category(category)
        assert client.get_good(payload.id) is None
        client.create_good(payload)
        client.update_good(payload)
        assert client.list_shop_ids() == ["shop-1"]
        client.set_shop_price(AqsiShopPricePayload.for_good(payload.id, "shop-1", payload.price))

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/pub/v2/GoodsCategory/list"),
        ("POST", "/pub/v2/GoodsCategory"),
        ("GET", "/pub/v2/Goods/variant-id"),
        ("POST", "/pub/v2/Goods"),
        ("PUT", "/pub/v2/Goods"),
        ("GET", "/pub/v2/Shops/list"),
        ("POST", "/pub/v2/Goods/prices"),
    ]
    assert all(
        request.headers["x-client-key"] == "Application test-aqsi-key" for request in requests
    )


def test_http_client_marks_server_errors_retryable_and_sanitizes_key(
    aqsi_settings: Settings,
) -> None:
    """Remote failures remain retryable where appropriate and never expose credentials."""
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(500, json={"errors": ["temporary failure"]})
    )

    with AqsiHttpClient(aqsi_settings, transport=transport) as client:
        with pytest.raises(AqsiApiError) as exc_info:
            client.category_exists("core-default-goods")

    assert exc_info.value.retryable is True
    assert "test-aqsi-key" not in str(exc_info.value)

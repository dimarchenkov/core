from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
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
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.pricing.schemas import PriceCreate
from core.pricing.service import (
    CurrentPriceNotFoundError,
    PriceService,
    PriceVariantNotFoundError,
    UnsupportedCurrencyError,
)
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database containing pricing dependencies."""
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
            Price.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create one active sellable catalog variant."""
    category = Category(title="Tools", slug="tools")
    product = CatalogProduct(
        title="Cordless drill",
        slug="cordless-drill",
        category=category,
    )
    variant = CatalogVariant(
        product=product,
        title="Blue",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={"color": "blue"},
    )
    session.add(variant)
    session.commit()
    return variant


@pytest.fixture
def user(session: Session) -> User:
    """Create an account for authenticated price writes."""
    return IdentityService(session).create_admin(
        "pricing-admin@example.com",
        "Pricing Admin",
        "long enough password",
    )


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the pricing test database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in the supplied user and return its bearer authorization header."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_set_price_appends_normalized_audited_fact(
    session: Session,
    variant: CatalogVariant,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a price rounds money and attributes the immutable fact."""
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1

    monkeypatch.setattr(session, "commit", count_commit)
    price = PriceService(session).set_price(
        variant.id,
        PriceCreate(price_type=PriceType.RETAIL, amount="125.555", reason="  Shelf price  "),
        actor_id=user.id,
    )

    assert price.amount == Decimal("125.56")
    assert price.currency == "RUB"
    assert price.reason == "Shelf price"
    assert price.created_by_id == user.id
    assert session.query(Price).count() == 1
    assert commit_calls == 0


def test_new_price_preserves_history_and_becomes_current(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """A later effective fact becomes current without changing the old fact."""
    service = PriceService(session)
    earlier = datetime(2026, 7, 1, tzinfo=UTC)
    later = earlier + timedelta(days=1)
    old_price = service.set_price(
        variant.id,
        PriceCreate(price_type="retail", amount="100", effective_from=earlier),
    )
    new_price = service.set_price(
        variant.id,
        PriceCreate(price_type="retail", amount="120", effective_from=later),
    )

    current = service.get_current_price(variant.id, PriceType.RETAIL, at=later)
    history = service.get_price_history(variant.id, price_type=PriceType.RETAIL)

    assert current.id == new_price.id
    assert old_price.amount == Decimal("100.00")
    assert [price.id for price in history] == [new_price.id, old_price.id]


def test_future_price_is_historical_but_not_current(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """A future price is visible in history and applies only from its effective time."""
    service = PriceService(session)
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    current = service.set_price(
        variant.id,
        PriceCreate(price_type="retail", amount="100", effective_from=now),
    )
    future = service.set_price(
        variant.id,
        PriceCreate(
            price_type="retail",
            amount="150",
            effective_from=now + timedelta(days=1),
        ),
    )

    assert service.get_current_price(variant.id, PriceType.RETAIL, at=now).id == current.id
    assert (
        service.get_current_price(
            variant.id,
            PriceType.RETAIL,
            at=now + timedelta(days=1),
        ).id
        == future.id
    )


def test_price_facts_reject_soft_delete_and_restore(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Normal model operations cannot erase immutable price history."""
    price = PriceService(session).set_price(
        variant.id,
        PriceCreate(price_type="retail", amount="100"),
    )

    with pytest.raises(RuntimeError, match="immutable"):
        price.soft_delete()
    with pytest.raises(RuntimeError, match="immutable"):
        price.restore()


def test_price_input_rejects_float_and_non_finite_values() -> None:
    """Public price input does not accept imprecise or non-finite money."""
    with pytest.raises(ValidationError):
        PriceCreate(price_type="retail", amount=12.34)
    with pytest.raises(ValidationError):
        PriceCreate(price_type="retail", amount="NaN")
    with pytest.raises(ValidationError):
        PriceCreate(price_type="retail", amount="Infinity")


def test_set_price_requires_active_variant_and_rubles(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """New price facts require an active variant and the supported currency."""
    service = PriceService(session)

    with pytest.raises(UnsupportedCurrencyError):
        service.set_price(
            variant.id,
            PriceCreate(price_type="retail", amount="100", currency="USD"),
        )

    variant.is_active = False
    session.commit()
    with pytest.raises(PriceVariantNotFoundError):
        service.set_price(
            variant.id,
            PriceCreate(price_type="retail", amount="100"),
        )


def test_missing_current_price_is_explicit(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """A variant without an applicable price has no implicit zero price."""
    with pytest.raises(CurrentPriceNotFoundError):
        PriceService(session).get_current_price(variant.id, PriceType.RETAIL)


def test_pricing_routes_are_authenticated_and_append_history(
    client: TestClient,
    session: Session,
    variant: CatalogVariant,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pricing API protects writes and exposes current and historical facts."""
    path = f"/api/pricing/variants/{variant.id}/prices"
    payload = {"price_type": "retail", "amount": "249.90", "reason": "Launch"}

    assert client.post(path, json=payload).status_code == 401

    headers = authorization_header(client, user)
    original_commit = session.commit
    commit_calls = 0

    def count_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(session, "commit", count_commit)
    created = client.post(path, headers=headers, json=payload)
    current = client.get(f"{path}/current?price_type=retail", headers=headers)
    history = client.get(f"{path}?price_type=retail", headers=headers)

    assert created.status_code == 201
    assert current.status_code == 200
    assert history.status_code == 200
    assert current.json()["id"] == created.json()["id"]
    assert history.json() == [created.json()]
    stored = session.get(Price, UUID(created.json()["id"]))
    assert stored is not None
    assert stored.created_by_id == user.id
    assert commit_calls == 1


def test_pricing_api_has_no_update_or_delete_operation(client: TestClient) -> None:
    """OpenAPI exposes no normal mutation path for historical price facts."""
    operations = client.get("/openapi.json").json()["paths"]

    assert all(
        "patch" not in methods and "delete" not in methods
        for path, methods in operations.items()
        if path.startswith("/api/pricing/")
    )


def test_price_migration_revision_identifier_is_short() -> None:
    """Price migration revision identifiers remain within Alembic's project limit."""
    migration_path = Path("migrations/versions/0012_create_prices.py")
    match = re.search(r'^revision: str = "([^"]+)"$', migration_path.read_text(), re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32

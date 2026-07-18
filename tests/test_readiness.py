from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.readiness.enums import ReadyForSaleRequirement
from core.readiness.service import ReadinessVariantNotFoundError, ReadyForSaleService
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database containing all readiness dependencies."""
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
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create an active variant with stable identifiers but no image or price."""
    category = Category(title="Tools", slug="tools")
    product = CatalogProduct(title="Drill", slug="drill", category=category)
    value = CatalogVariant(
        product=product,
        title="Blue",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={},
    )
    session.add(value)
    session.commit()
    return value


@pytest.fixture
def user(session: Session) -> User:
    """Create an account for authenticated readiness requests."""
    return IdentityService(session).create_admin(
        "readiness-admin@example.com",
        "Readiness Admin",
        "long enough password",
    )


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the readiness test database."""
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


def add_primary_image(session: Session, variant: CatalogVariant) -> Image:
    """Attach one active primary image to the supplied variant."""
    image = Image(
        source_key="images/source/drill.jpg",
        original_filename="drill.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=800,
        height=600,
        checksum="sha256:drill",
    )
    image.links.append(
        ImageLink(
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=variant.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    session.add(image)
    session.commit()
    return image


def add_retail_price(
    session: Session,
    variant: CatalogVariant,
    amount: Decimal = Decimal("249.00"),
    *,
    effective_from: datetime | None = None,
) -> Price:
    """Add one retail price fact for readiness tests."""
    price = Price(
        variant_id=variant.id,
        price_type=PriceType.RETAIL,
        amount=amount,
        currency="RUB",
        effective_from=effective_from or datetime.now(UTC),
    )
    session.add(price)
    session.commit()
    return price


def test_complete_variant_is_ready_for_sale(session: Session, variant: CatalogVariant) -> None:
    """An active photographed, identified, and positively priced variant is ready."""
    add_primary_image(session, variant)
    add_retail_price(session, variant)

    result = ReadyForSaleService(session).check_variant(variant.id)

    assert result.is_ready is True
    assert result.missing_requirements == []


def test_readiness_returns_all_missing_requirements_in_stable_order(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """One check returns every actionable reason instead of stopping at the first."""
    variant.is_active = False
    variant.sku = ""
    variant.barcode = ""
    session.commit()

    result = ReadyForSaleService(session).check_variant(variant.id)

    assert result.is_ready is False
    assert result.missing_requirements == [
        ReadyForSaleRequirement.INACTIVE_VARIANT,
        ReadyForSaleRequirement.MISSING_PRIMARY_IMAGE,
        ReadyForSaleRequirement.MISSING_SKU,
        ReadyForSaleRequirement.MISSING_BARCODE,
        ReadyForSaleRequirement.MISSING_RETAIL_PRICE,
    ]


def test_invalid_legacy_barcode_is_reported(session: Session, variant: CatalogVariant) -> None:
    """Legacy barcode data must satisfy AQSI's numeric length contract."""
    variant.barcode = "ABC"
    session.commit()

    result = ReadyForSaleService(session).check_variant(variant.id)

    assert ReadyForSaleRequirement.INVALID_BARCODE in result.missing_requirements


def test_zero_promo_and_future_prices_do_not_satisfy_retail_requirement(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Only a positive currently effective retail price makes a Variant sellable."""
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    session.add_all(
        [
            Price(
                variant_id=variant.id,
                price_type=PriceType.PROMO,
                amount=Decimal("100"),
                currency="RUB",
                effective_from=now,
            ),
            Price(
                variant_id=variant.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("0"),
                currency="RUB",
                effective_from=now,
            ),
            Price(
                variant_id=variant.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("200"),
                currency="RUB",
                effective_from=now + timedelta(days=1),
            ),
        ]
    )
    session.commit()

    result = ReadyForSaleService(session).check_variant(variant.id, at=now)

    assert ReadyForSaleRequirement.MISSING_RETAIL_PRICE in result.missing_requirements


def test_deleted_image_does_not_satisfy_photo_first(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """An active link to a deleted image does not satisfy the image requirement."""
    image = add_primary_image(session, variant)
    image.soft_delete()
    session.commit()

    result = ReadyForSaleService(session).check_variant(variant.id)

    assert ReadyForSaleRequirement.MISSING_PRIMARY_IMAGE in result.missing_requirements


def test_archived_or_missing_variant_has_no_readiness_result(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Archived variants remain historical and are excluded from active readiness work."""
    variant.soft_delete()
    session.commit()

    with pytest.raises(ReadinessVariantNotFoundError):
        ReadyForSaleService(session).check_variant(variant.id)


def test_readiness_api_is_authenticated_and_returns_machine_codes(
    client: TestClient,
    variant: CatalogVariant,
    user: User,
) -> None:
    """Readiness API is protected and returns stable codes suitable for any UI."""
    path = f"/api/readiness/variants/{variant.id}/ready-for-sale"

    assert client.get(path).status_code == 401

    response = client.get(path, headers=authorization_header(client, user))

    assert response.status_code == 200
    assert response.json() == {
        "variant_id": str(variant.id),
        "is_ready": False,
        "missing_requirements": ["missing_primary_image", "missing_retail_price"],
    }

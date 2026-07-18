from __future__ import annotations

from collections.abc import Generator
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
from core.labels.renderer import VariantLabel58x40Renderer, VariantLabelData
from core.labels.service import LabelVariantNotReadyError, VariantLabelService
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.readiness.enums import ReadyForSaleRequirement
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database containing label dependencies."""
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
    """Create a photographed and positively priced sellable Variant."""
    category = Category(title="Хранение", slug="storage")
    product = CatalogProduct(
        title="Обувница пластиковая",
        slug="plastic-shoe-rack",
        category=category,
    )
    value = CatalogVariant(
        product=product,
        title="6 ярусов",
        sku="SKU-000001",
        barcode="2000000000015",
        attributes={"color": "бежевая"},
    )
    session.add(value)
    session.flush()
    image = Image(
        source_key="images/source/shoe-rack.jpg",
        original_filename="shoe-rack.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=800,
        height=600,
        checksum="sha256:shoe-rack",
    )
    image.links.append(
        ImageLink(
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=value.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    price = Price(
        variant_id=value.id,
        price_type=PriceType.RETAIL,
        amount=Decimal("1299.00"),
        currency="RUB",
        effective_from=value.created_at,
    )
    session.add_all([image, price])
    session.commit()
    return value


@pytest.fixture
def user(session: Session) -> User:
    """Create an account for authenticated label requests."""
    return IdentityService(session).create_admin(
        "label-admin@example.com",
        "Label Admin",
        "long enough password",
    )


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the label test database."""
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


def test_renderer_creates_single_pdf_label_with_cyrillic_data() -> None:
    """The 58 x 40 renderer accepts real Russian catalog text and produces PDF bytes."""
    content = VariantLabel58x40Renderer().render(
        VariantLabelData(
            product_title="Обувница пластиковая шестиъярусная",
            variant_details="6 ярусов - бежевая",
            price=Decimal("1299.00"),
            barcode="2000000000015",
            sku="SKU-000001",
        )
    )

    assert content.startswith(b"%PDF-")
    assert len(content) > 5_000


def test_label_service_uses_authoritative_variant_and_price(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Label generation resolves catalog and current price data instead of accepting a payload."""
    content = VariantLabelService(session).generate_58x40(variant.id)

    assert content.startswith(b"%PDF-")


def test_label_service_rejects_variant_before_ready_for_sale(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Photo First and pricing requirements block premature label generation."""
    variant.is_active = False
    session.commit()

    with pytest.raises(LabelVariantNotReadyError) as error:
        VariantLabelService(session).generate_58x40(variant.id)

    assert ReadyForSaleRequirement.INACTIVE_VARIANT in error.value.missing_requirements


def test_label_api_is_authenticated_and_returns_inline_pdf(
    client: TestClient,
    variant: CatalogVariant,
    user: User,
) -> None:
    """The label endpoint is protected and returns a directly printable PDF."""
    path = f"/api/labels/variants/{variant.id}/58x40.pdf"

    assert client.get(path).status_code == 401

    response = client.get(path, headers=authorization_header(client, user))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.content.startswith(b"%PDF-")

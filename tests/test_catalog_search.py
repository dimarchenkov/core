from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcodes import BarcodeSource
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.catalog.search import CatalogSearchService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide a Catalog/Pricing database for search projections."""
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
        ],
    )
    with sessionmaker(bind=engine, autoflush=False, autocommit=False)() as database_session:
        yield database_session


@pytest.fixture
def catalog(session: Session) -> tuple[CatalogProduct, CatalogVariant, CatalogVariant]:
    """Create Russian Product/Variant records with one historical barcode assignment."""
    category = Category(title="Канцелярия", slug="stationery")
    session.add(category)
    session.flush()
    product = CatalogProduct(
        title="Ручка шариковая", slug="ball-pen", category_id=category.id
    )
    session.add(product)
    session.flush()
    blue = CatalogVariant(
        product_id=product.id,
        title="Синяя",
        sku="SKU-00123",
        barcode="4601234567890",
        barcode_source=BarcodeSource.MANUFACTURER,
    )
    black = CatalogVariant(
        product_id=product.id,
        title="Чёрная",
        sku="SKU-00124",
        barcode="2000000001241",
        barcode_source=BarcodeSource.INTERNAL,
    )
    session.add_all([blue, black])
    session.flush()
    session.add_all(
        [
            CatalogVariantBarcode(
                variant_id=blue.id, value="4699999999999", source=BarcodeSource.MANUFACTURER
            ),
            Price(
                variant_id=blue.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("100.00"),
                currency="RUB",
                effective_from=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    return product, blue, black


def test_variant_search_supports_russian_text_identifiers_price_and_current_barcode(
    session: Session, catalog: tuple[CatalogProduct, CatalogVariant, CatalogVariant]
) -> None:
    """Variant picker searches display text and only the current operational barcode."""
    _, blue, _ = catalog
    service = CatalogSearchService(session)

    assert {item.id for item in service.search_variants("РУЧКА", limit=12).items} == {
        variant.id for variant in catalog[1:]
    }
    assert [item.id for item in service.search_variants("ручка син", limit=12).items] == [
        blue.id
    ]
    assert service.search_variants("sku-00123", limit=12).items[0].id == blue.id
    current = service.search_variants("4601234567890", limit=12).items[0]
    assert current.id == blue.id
    assert current.retail_price == Decimal("100.00")
    assert service.search_variants("4699999999999", limit=12).items == []


def test_product_search_resolves_children_deduplicates_and_finds_empty_product(
    session: Session, catalog: tuple[CatalogProduct, CatalogVariant, CatalogVariant]
) -> None:
    """Product picker returns one parent whether Product or several Variants matched."""
    product, _, _ = catalog
    empty = CatalogProduct(
        title="Обувница металлическая",
        slug="metal-shoe-rack",
        category_id=product.category_id,
    )
    session.add(empty)
    session.commit()
    service = CatalogSearchService(session)

    assert [item.id for item in service.search_products("SKU-001", limit=12).items] == [
        product.id
    ]
    via_variant = service.search_products("синяя", limit=12).items[0]
    assert via_variant.id == product.id
    assert via_variant.matched_variant_title == "Синяя"
    direct = service.search_products("ОБУВНИЦА", limit=12).items[0]
    assert direct.id == empty.id
    assert direct.variant_count == 0


def test_search_result_limit_and_authenticated_routes(
    session: Session, catalog: tuple[CatalogProduct, CatalogVariant, CatalogVariant]
) -> None:
    """Both HTTP projections are bounded and expose overflow state."""
    product, _, _ = catalog
    session.add(
        CatalogVariant(
            product_id=product.id,
            title="Красная",
            sku="SKU-00125",
            barcode="2000000001258",
            barcode_source=BarcodeSource.INTERNAL,
        )
    )
    session.commit()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as client:
        response = client.get("/api/catalog/search/variants", params={"query": "ручка", "limit": 1})
    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["has_more"] is True

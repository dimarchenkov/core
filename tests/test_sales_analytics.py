from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.analytics.read_service import SalesAnalyticsReadService, resolve_sales_period
from core.analytics.schemas import SalesPeriodPreset, SalesRanking
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.config import get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.sales.enums import SaleItemSource, SaleStatus
from core.sales.models import Sale, SaleItem
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide complete SQLite metadata for reporting query tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def analytics_setup(
    session: Session,
) -> tuple[User, CatalogProduct, CatalogVariant, CatalogVariant]:
    """Create one Product with two independently identifiable Variants."""
    user = User(email="analytics@example.com", full_name="Analytics", password_hash="unused")
    category = Category(title="Analytics", slug="analytics")
    product = CatalogProduct(title="Сквиш", slug="squish", category=category)
    red = CatalogVariant(
        product=product,
        title="Красный",
        sku="ANALYTICS-RED",
        barcode="ANALYTICS-RED-CODE",
        attributes={},
    )
    blue = CatalogVariant(
        product=product,
        title="Синий",
        sku="ANALYTICS-BLUE",
        barcode="ANALYTICS-BLUE-CODE",
        attributes={},
    )
    session.add_all([user, red, blue])
    session.commit()
    return user, product, red, blue


def _sale(
    session: Session,
    user: User,
    *,
    number: int,
    completed_at: datetime,
    lines: list[tuple[CatalogVariant | str | None, int, Decimal, Decimal]],
    status: SaleStatus = SaleStatus.COMPLETED,
) -> Sale:
    """Persist a Sale with frozen item values and no mutation-service side effects."""
    total = sum((line_total for _, _, _, line_total in lines), start=Decimal("0.00"))
    sale = Sale(
        sale_number=number,
        owner_id=user.id,
        status=status,
        subtotal_amount=total,
        discount_amount=Decimal("0.00"),
        total_amount=total,
        completed_at=completed_at if status is SaleStatus.COMPLETED else None,
        paid_at=completed_at if status is SaleStatus.COMPLETED else None,
    )
    for index, (variant, quantity, unit_price, line_total) in enumerate(lines):
        catalog_variant = variant if isinstance(variant, CatalogVariant) else None
        manual_name = variant if isinstance(variant, str) else f"Свободная позиция {index}"
        source = (
            SaleItemSource.CATALOG
            if catalog_variant is not None
            else SaleItemSource.MANUAL
        )
        sale.items.append(
            SaleItem(
                source=source,
                variant_id=catalog_variant.id if catalog_variant is not None else None,
                product_title_snapshot=(
                    catalog_variant.product.title if catalog_variant is not None else None
                ),
                variant_title_snapshot=(
                    catalog_variant.title if catalog_variant is not None else None
                ),
                display_label_snapshot=(
                    f"{catalog_variant.product.title} · {catalog_variant.title}"
                    if catalog_variant is not None
                    else manual_name
                ),
                sku_snapshot=catalog_variant.sku if catalog_variant is not None else None,
                barcode_snapshot=(
                    catalog_variant.barcode if catalog_variant is not None else None
                ),
                unit_price=unit_price,
                quantity=quantity,
                line_total=line_total,
                fiscal_line_total=line_total,
            )
        )
    session.add(sale)
    session.commit()
    return sale


def _october_period() -> object:
    """Resolve the fixed custom period shared by aggregation tests."""
    return resolve_sales_period(
        SalesPeriodPreset.CUSTOM,
        "Europe/Moscow",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 18),
        now=datetime(2026, 10, 16, 12, tzinfo=UTC),
    )


def test_empty_period_returns_stable_zero_metrics(session: Session) -> None:
    """No Sales produces numeric zeroes rather than nulls or division errors."""
    summary = SalesAnalyticsReadService(session, "Europe/Moscow").summary(_october_period())

    assert summary.revenue == Decimal("0.00")
    assert summary.receipt_count == 0
    assert summary.average_receipt == Decimal("0.00")
    assert summary.items_sold == 0


def test_only_completed_sales_count_and_all_item_quantities_are_summed(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """Draft, cancelled and failed lifecycle states are never treated as revenue."""
    user, _, red, _ = analytics_setup
    moment = datetime(2026, 10, 15, 9, tzinfo=UTC)
    _sale(
        session,
        user,
        number=1,
        completed_at=moment,
        lines=[(red, 2, Decimal("50.00"), Decimal("100.00"))],
    )
    for number, status in enumerate(
        (SaleStatus.DRAFT, SaleStatus.CANCELLED, SaleStatus.PAYMENT_FAILED), start=2
    ):
        _sale(
            session,
            user,
            number=number,
            completed_at=moment,
            lines=[(red, 9, Decimal("50.00"), Decimal("450.00"))],
            status=status,
        )

    summary = SalesAnalyticsReadService(session, "Europe/Moscow").summary(_october_period())

    assert summary.revenue == Decimal("100.00")
    assert summary.receipt_count == 1
    assert summary.average_receipt == Decimal("100.00")
    assert summary.items_sold == 2


def test_catalog_and_free_sale_share_one_receipt_and_have_exact_source_breakdown(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """One mixed Sale contributes once to receipts and both sources to totals."""
    user, _, red, _ = analytics_setup
    _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
        lines=[
            (red, 2, Decimal("100.00"), Decimal("200.00")),
            ("Редкий сувенир", 3, Decimal("50.00"), Decimal("150.00")),
        ],
    )

    summary = SalesAnalyticsReadService(session, "Europe/Moscow").summary(_october_period())

    assert summary.revenue == Decimal("350.00")
    assert summary.receipt_count == 1
    assert summary.average_receipt == Decimal("350.00")
    assert summary.items_sold == 5
    assert summary.catalog_revenue == Decimal("200.00")
    assert summary.free_sale_revenue == Decimal("150.00")
    assert summary.catalog_units == 2
    assert summary.free_sale_units == 3
    assert summary.free_sale_revenue_share == Decimal("0.4286")
    assert summary.free_sale_units_share == Decimal("0.6000")


def test_free_sale_grouping_normalizes_only_case_and_whitespace(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """Safe name variants group, distinct words do not, and historical text is untouched."""
    user, _, _, _ = analytics_setup
    sale = _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
        lines=[
            ("Сквиш куб", 1, Decimal("50.00"), Decimal("50.00")),
            (" сквиш куб ", 2, Decimal("60.00"), Decimal("120.00")),
            ("СКВИШ   КУБ", 3, Decimal("70.00"), Decimal("210.00")),
            ("Сквиш куб большой", 4, Decimal("80.00"), Decimal("320.00")),
        ],
    )

    groups = SalesAnalyticsReadService(session, "Europe/Moscow").top_free_sale_items(
        _october_period(), ranking=SalesRanking.UNITS
    )

    assert len(groups) == 2
    assert groups[0].normalized_name == "сквиш куб"
    assert groups[0].units == 6
    assert groups[0].revenue == Decimal("380.00")
    assert groups[1].normalized_name == "сквиш куб большой"
    assert [item.display_label_snapshot for item in sale.items] == [
        "Сквиш куб",
        " сквиш куб ",
        "СКВИШ   КУБ",
        "Сквиш куб большой",
    ]


def test_free_sale_same_name_groups_different_prices_without_catalog_collision(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """Price and Catalog name equality never redefine free-sale identity."""
    user, _, red, _ = analytics_setup
    catalog_label = f"{red.product.title} · {red.title}"
    _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 14, 10, tzinfo=UTC),
        lines=[
            (red, 1, Decimal("90.00"), Decimal("90.00")),
            (catalog_label, 2, Decimal("50.00"), Decimal("100.00")),
            (catalog_label.lower(), 1, Decimal("70.00"), Decimal("70.00")),
        ],
    )
    service = SalesAnalyticsReadService(session, "Europe/Moscow")

    products = service.top_products(_october_period())
    free_items = service.top_free_sale_items(_october_period())

    assert products[0].units == 1
    assert products[0].revenue == Decimal("90.00")
    assert free_items[0].units == 3
    assert free_items[0].revenue == Decimal("170.00")
    assert free_items[0].normalized_name == catalog_label.casefold()


def test_historical_values_product_aggregation_and_independent_rankings(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """Reports use frozen prices and aggregate Variants while keeping Variant identity."""
    user, product, red, blue = analytics_setup
    _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 14, 10, tzinfo=UTC),
        lines=[
            (red, 3, Decimal("50.00"), Decimal("150.00")),
            (blue, 2, Decimal("70.00"), Decimal("140.00")),
        ],
    )
    expensive = CatalogProduct(
        title="Редкий товар",
        slug="rare",
        category=product.category,
    )
    expensive_variant = CatalogVariant(
        product=expensive,
        title="Основной",
        sku="ANALYTICS-RARE",
        barcode="ANALYTICS-RARE-CODE",
        attributes={},
    )
    session.add(expensive_variant)
    session.commit()
    _sale(
        session,
        user,
        number=2,
        completed_at=datetime(2026, 10, 14, 11, tzinfo=UTC),
        lines=[(expensive_variant, 1, Decimal("400.00"), Decimal("400.00"))],
    )
    red.product.title = "Переименованный товар"
    session.commit()
    service = SalesAnalyticsReadService(session, "Europe/Moscow")

    products_by_units = service.top_products(
        _october_period(), ranking=SalesRanking.UNITS
    )
    products_by_revenue = service.top_products(
        _october_period(), ranking=SalesRanking.REVENUE
    )
    variants = service.top_variants(_october_period(), ranking=SalesRanking.UNITS)

    assert products_by_units[0].product_title == "Сквиш"
    assert products_by_units[0].units == 5
    assert products_by_units[0].revenue == Decimal("290.00")
    assert products_by_revenue[0].product_title == "Редкий товар"
    assert [item.units for item in variants[:2]] == [3, 2]
    assert variants[0].display_label == "Сквиш · Красный"


def test_local_midnight_grouping_and_weekday_observed_day_average(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """UTC events group after conversion to the configured business-local date."""
    user, _, red, _ = analytics_setup
    _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 11, 22, 30, tzinfo=UTC),
        lines=[(red, 1, Decimal("50.00"), Decimal("50.00"))],
    )
    _sale(
        session,
        user,
        number=2,
        completed_at=datetime(2026, 10, 18, 22, 30, tzinfo=UTC),
        lines=[(red, 1, Decimal("70.00"), Decimal("70.00"))],
    )
    period = resolve_sales_period(
        SalesPeriodPreset.CUSTOM,
        "Europe/Moscow",
        start_date=date(2026, 10, 12),
        end_date=date(2026, 10, 19),
    )
    service = SalesAnalyticsReadService(session, "Europe/Moscow")
    timeline = service.timeline(period)
    monday = service.weekdays(period, daily=timeline)[0]

    assert timeline[0].date == date(2026, 10, 12)
    assert timeline[0].revenue == Decimal("50.00")
    assert timeline[-1].date == date(2026, 10, 19)
    assert timeline[-1].revenue == Decimal("70.00")
    assert monday.observed_days == 2
    assert monday.revenue == Decimal("120.00")
    assert monday.average_revenue == Decimal("60.00")


def test_presets_use_business_calendar_boundaries() -> None:
    """Today, trailing calendar-day presets and current month resolve deterministically."""
    now = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)

    today = resolve_sales_period(SalesPeriodPreset.TODAY, "Europe/Moscow", now=now)
    seven = resolve_sales_period(SalesPeriodPreset.SEVEN_DAYS, "Europe/Moscow", now=now)
    thirty = resolve_sales_period(SalesPeriodPreset.THIRTY_DAYS, "Europe/Moscow", now=now)
    month = resolve_sales_period(SalesPeriodPreset.THIS_MONTH, "Europe/Moscow", now=now)

    assert today.start_date == today.end_date == date(2026, 10, 16)
    assert seven.start_date == date(2026, 10, 10)
    assert thirty.start_date == date(2026, 9, 17)
    assert month.start_date == date(2026, 10, 1)
    assert month.end_date == date(2026, 10, 16)


def test_analytics_api_exposes_dashboard_and_validates_custom_period(
    session: Session,
    analytics_setup: tuple[User, CatalogProduct, CatalogVariant, CatalogVariant],
) -> None:
    """Authenticated clients receive stable read models and human-readable period errors."""
    user, _, red, _ = analytics_setup
    _sale(
        session,
        user,
        number=1,
        completed_at=datetime(2026, 10, 15, 9, tzinfo=UTC),
        lines=[(red, 1, Decimal("50.00"), Decimal("50.00"))],
    )
    app = create_app()

    def override_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_settings] = get_settings
    with TestClient(app) as client:
        response = client.get(
            "/api/analytics/sales",
            params={
                "period": "custom",
                "start_date": "2026-10-15",
                "end_date": "2026-10-15",
            },
        )
        free_sale = client.get(
            "/api/analytics/sales/top-free-sale-items",
            params={
                "period": "custom",
                "start_date": "2026-10-15",
                "end_date": "2026-10-15",
            },
        )
        invalid = client.get("/api/analytics/sales/summary", params={"period": "custom"})

    assert response.status_code == 200
    assert response.json()["summary"]["revenue"] == "50.00"
    assert response.json()["summary"]["catalog_revenue"] == "50.00"
    assert response.json()["summary"]["free_sale_revenue"] == "0.00"
    assert response.json()["top_free_sale_items"] == []
    assert response.json()["daily"][0]["date"] == "2026-10-15"
    assert free_sale.status_code == 200
    assert free_sale.json() == []
    assert invalid.status_code == 422
    assert "start_date" in invalid.json()["detail"]

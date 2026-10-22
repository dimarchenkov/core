from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from core.shared.db import UUIDv7


class SalesPeriodPreset(StrEnum):
    """Supported business-calendar shortcuts for Sales analytics."""

    TODAY = "today"
    SEVEN_DAYS = "7_days"
    THIRTY_DAYS = "30_days"
    THIS_MONTH = "this_month"
    CUSTOM = "custom"


class SalesRanking(StrEnum):
    """Supported ranking dimensions for Product and Variant tables."""

    REVENUE = "revenue"
    UNITS = "units"


class SalesAnalyticsPeriodRead(PydanticBaseModel):
    """Resolved local dates and UTC boundaries used by every report query."""

    preset: SalesPeriodPreset
    start_date: date
    end_date: date
    start_at: datetime
    end_at: datetime
    timezone: str


class SalesSummaryRead(PydanticBaseModel):
    """Primary business, Sales and Rental KPIs for one resolved period."""

    period: SalesAnalyticsPeriodRead
    business_revenue: Decimal
    sales_revenue: Decimal
    rental_revenue: Decimal
    overdue_revenue: Decimal
    sale_count: int
    rental_count: int
    average_rental_amount: Decimal
    # Compatibility fields retain their original Sales-only meaning.
    revenue: Decimal
    receipt_count: int
    average_receipt: Decimal
    items_sold: int
    catalog_revenue: Decimal
    free_sale_revenue: Decimal
    catalog_units: int
    free_sale_units: int
    free_sale_revenue_share: Decimal
    free_sale_units_share: Decimal
    currency: str = "RUB"


class SalesDailyRead(PydanticBaseModel):
    """Exact Sales, Rental and combined metrics for one business-local date."""

    date: date
    sales_revenue: Decimal
    rental_revenue: Decimal
    total_revenue: Decimal
    sale_count: int
    rental_count: int
    # Compatibility fields retain their original Sales-only meaning.
    revenue: Decimal
    receipt_count: int
    items_sold: int
    average_receipt: Decimal


class SalesProductRead(PydanticBaseModel):
    """Sales aggregated from Variant snapshots into their parent Product identity."""

    product_id: UUIDv7
    product_title: str
    units: int
    revenue: Decimal
    currency: str = "RUB"


class SalesVariantRead(PydanticBaseModel):
    """Sales for one preserved Variant identity and historical display snapshot."""

    variant_id: UUIDv7
    product_title: str
    variant_title: str
    display_label: str
    sku: str | None
    units: int
    revenue: Decimal
    currency: str = "RUB"


class SalesFreeSaleItemRead(PydanticBaseModel):
    """Conservatively grouped manual SaleItem snapshots without Catalog identity."""

    normalized_name: str
    display_name: str
    units: int
    revenue: Decimal
    currency: str = "RUB"


class RentalAssetAnalyticsRead(PydanticBaseModel):
    """Completed rental economics for one immutable physical RentalAsset identity."""

    rental_asset_id: UUIDv7
    asset_number: str
    title: str
    rental_count: int
    revenue: Decimal
    currency: str = "RUB"


class SalesWeekdayRead(PydanticBaseModel):
    """Aggregated performance for one weekday across the selected period."""

    weekday: int = Field(ge=1, le=7)
    label: str
    observed_days: int
    revenue: Decimal
    average_revenue: Decimal
    receipt_count: int
    average_receipt: Decimal


class SalesPaymentMethodRead(PydanticBaseModel):
    """Completed-sale totals for one reliably stored tender method."""

    payment_method: str
    label: str
    revenue: Decimal
    receipt_count: int


class SalesAnalyticsDashboardRead(PydanticBaseModel):
    """Complete first-load read model for the business analytics workspace."""

    summary: SalesSummaryRead
    timeline: list[SalesDailyRead]
    daily: list[SalesDailyRead]
    top_products: list[SalesProductRead]
    top_variants: list[SalesVariantRead]
    top_free_sale_items: list[SalesFreeSaleItemRead]
    top_rental_items: list[RentalAssetAnalyticsRead]
    weekdays: list[SalesWeekdayRead]
    payment_methods: list[SalesPaymentMethodRead]
    limitations: list[str]

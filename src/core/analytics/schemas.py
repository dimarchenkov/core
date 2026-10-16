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
    """Primary Sales KPIs for one resolved period."""

    period: SalesAnalyticsPeriodRead
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
    """Exact metrics for one business-local calendar date."""

    date: date
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
    """Complete first-load read model for the Sales analytics workspace."""

    summary: SalesSummaryRead
    timeline: list[SalesDailyRead]
    daily: list[SalesDailyRead]
    top_products: list[SalesProductRead]
    top_variants: list[SalesVariantRead]
    top_free_sale_items: list[SalesFreeSaleItemRead]
    weekdays: list[SalesWeekdayRead]
    payment_methods: list[SalesPaymentMethodRead]
    limitations: list[str]

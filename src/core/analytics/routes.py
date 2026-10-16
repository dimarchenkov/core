from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.analytics.read_service import (
    SalesAnalyticsPeriod,
    SalesAnalyticsPeriodError,
    SalesAnalyticsReadService,
    resolve_sales_period,
)
from core.analytics.schemas import (
    SalesAnalyticsDashboardRead,
    SalesDailyRead,
    SalesFreeSaleItemRead,
    SalesPeriodPreset,
    SalesProductRead,
    SalesRanking,
    SalesSummaryRead,
    SalesVariantRead,
    SalesWeekdayRead,
)
from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user

router = APIRouter(
    prefix="/api/analytics/sales",
    tags=["sales-analytics"],
    dependencies=[Depends(get_current_user)],
)


def get_sales_analytics_period(
    settings: Annotated[Settings, Depends(get_settings)],
    period: SalesPeriodPreset = SalesPeriodPreset.TODAY,
    start_date: date | None = None,
    end_date: date | None = None,
) -> SalesAnalyticsPeriod:
    """Validate and resolve the shared calendar-period query parameters."""
    try:
        return resolve_sales_period(
            period,
            settings.rental_timezone,
            start_date=start_date,
            end_date=end_date,
        )
    except SalesAnalyticsPeriodError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def get_sales_analytics_service(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SalesAnalyticsReadService:
    """Provide transaction-neutral Sales reporting queries for one request."""
    return SalesAnalyticsReadService(session, settings.rental_timezone)


@router.get("", response_model=SalesAnalyticsDashboardRead)
def sales_dashboard(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
    ranking: SalesRanking = SalesRanking.REVENUE,
    limit: int = Query(default=10, ge=1, le=100),
) -> SalesAnalyticsDashboardRead:
    """Return the complete reusable read model used by the Analytics workspace."""
    return service.dashboard(report_period, ranking=ranking, limit=limit)


@router.get("/summary", response_model=SalesSummaryRead)
def sales_summary(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
) -> SalesSummaryRead:
    """Return primary revenue and volume KPIs."""
    return service.summary(report_period)


@router.get("/timeline", response_model=list[SalesDailyRead])
def sales_timeline(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
) -> list[SalesDailyRead]:
    """Return exact day-grouped revenue, receipt and item values."""
    return service.timeline(report_period)


@router.get("/daily", response_model=list[SalesDailyRead])
def sales_daily(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
) -> list[SalesDailyRead]:
    """Return the exact daily table newest first."""
    return list(reversed(service.timeline(report_period)))


@router.get("/top-products", response_model=list[SalesProductRead])
def sales_top_products(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
    ranking: SalesRanking = SalesRanking.REVENUE,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[SalesProductRead]:
    """Rank Products by units or final historical revenue."""
    return service.top_products(report_period, ranking=ranking, limit=limit)


@router.get("/top-variants", response_model=list[SalesVariantRead])
def sales_top_variants(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
    ranking: SalesRanking = SalesRanking.REVENUE,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[SalesVariantRead]:
    """Rank Variant identities by units or final historical revenue."""
    return service.top_variants(report_period, ranking=ranking, limit=limit)


@router.get("/top-free-sale-items", response_model=list[SalesFreeSaleItemRead])
def sales_top_free_sale_items(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
    ranking: SalesRanking = SalesRanking.REVENUE,
    limit: int = Query(default=10, ge=1, le=100),
) -> list[SalesFreeSaleItemRead]:
    """Rank manual/free-sale item groups without inventing Catalog identity."""
    return service.top_free_sale_items(report_period, ranking=ranking, limit=limit)


@router.get("/weekdays", response_model=list[SalesWeekdayRead])
def sales_weekdays(
    report_period: Annotated[SalesAnalyticsPeriod, Depends(get_sales_analytics_period)],
    service: Annotated[SalesAnalyticsReadService, Depends(get_sales_analytics_service)],
) -> list[SalesWeekdayRead]:
    """Return weekday totals and per-observed-day averages."""
    return service.weekdays(report_period)

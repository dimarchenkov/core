from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from core.analytics.schemas import (
    SalesAnalyticsDashboardRead,
    SalesAnalyticsPeriodRead,
    SalesDailyRead,
    SalesFreeSaleItemRead,
    SalesPaymentMethodRead,
    SalesPeriodPreset,
    SalesProductRead,
    SalesRanking,
    SalesSummaryRead,
    SalesVariantRead,
    SalesWeekdayRead,
)
from core.catalog.models import CatalogVariant
from core.sales.enums import PaymentStatus, SaleItemSource, SaleStatus
from core.sales.models import PaymentAttempt, Sale, SaleItem

ZERO_MONEY = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")
WEEKDAY_LABELS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def normalize_free_sale_name(value: str) -> str:
    """Build a conservative grouping key without changing stored SaleItem text."""
    return " ".join(value.split()).casefold()


class SalesAnalyticsPeriodError(ValueError):
    """Raised when a requested calendar period is incomplete or invalid."""


@dataclass(frozen=True)
class SalesAnalyticsPeriod:
    """Local inclusive dates with UTC half-open boundaries for report queries."""

    preset: SalesPeriodPreset
    start_date: date
    end_date: date
    start_at: datetime
    end_at: datetime
    timezone: str

    def read_model(self) -> SalesAnalyticsPeriodRead:
        """Expose the exact interpreted period to API clients."""
        return SalesAnalyticsPeriodRead(
            preset=self.preset,
            start_date=self.start_date,
            end_date=self.end_date,
            start_at=self.start_at,
            end_at=self.end_at,
            timezone=self.timezone,
        )


def resolve_sales_period(
    preset: SalesPeriodPreset,
    timezone_name: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> SalesAnalyticsPeriod:
    """Resolve operator-facing local dates into an exact UTC half-open interval."""
    try:
        business_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SalesAnalyticsPeriodError("Неизвестный часовой пояс аналитики") from exc
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_today = current.astimezone(business_timezone).date()
    if preset is SalesPeriodPreset.TODAY:
        local_start = local_end = local_today
    elif preset is SalesPeriodPreset.SEVEN_DAYS:
        local_start, local_end = local_today - timedelta(days=6), local_today
    elif preset is SalesPeriodPreset.THIRTY_DAYS:
        local_start, local_end = local_today - timedelta(days=29), local_today
    elif preset is SalesPeriodPreset.THIS_MONTH:
        local_start, local_end = local_today.replace(day=1), local_today
    else:
        if start_date is None or end_date is None:
            raise SalesAnalyticsPeriodError(
                "Для произвольного периода укажите start_date и end_date"
            )
        local_start, local_end = start_date, end_date
    if local_start > local_end:
        raise SalesAnalyticsPeriodError("Начало периода не может быть позже окончания")
    if (local_end - local_start).days > 730:
        raise SalesAnalyticsPeriodError("Период аналитики не должен превышать 731 день")
    start_local = datetime.combine(local_start, time.min, tzinfo=business_timezone)
    end_local = datetime.combine(local_end + timedelta(days=1), time.min, tzinfo=business_timezone)
    return SalesAnalyticsPeriod(
        preset=preset,
        start_date=local_start,
        end_date=local_end,
        start_at=start_local.astimezone(UTC),
        end_at=end_local.astimezone(UTC),
        timezone=timezone_name,
    )


class SalesAnalyticsReadService:
    """Run read-only aggregate queries over immutable completed Sales facts."""

    def __init__(self, session: Session, timezone_name: str) -> None:
        """Bind reporting queries to a session and the configured business timezone."""
        self._session = session
        self._timezone_name = timezone_name

    def dashboard(
        self,
        period: SalesAnalyticsPeriod,
        *,
        limit: int = 10,
        ranking: SalesRanking = SalesRanking.REVENUE,
    ) -> SalesAnalyticsDashboardRead:
        """Return the complete Sales analytics page without mutating domain records."""
        timeline = self.timeline(period)
        return SalesAnalyticsDashboardRead(
            summary=self.summary(period),
            timeline=timeline,
            daily=list(reversed(timeline)),
            top_products=self.top_products(period, limit=limit, ranking=ranking),
            top_variants=self.top_variants(period, limit=limit, ranking=ranking),
            top_free_sale_items=self.top_free_sale_items(
                period, limit=limit, ranking=ranking
            ),
            weekdays=self.weekdays(period, daily=timeline),
            payment_methods=self.payment_methods(period),
            limitations=[
                "Возвраты и возмещения пока не реализованы в Sales; "
                "выручка не уменьшается на refunds.",
                "Себестоимость, валовая прибыль и маржа не рассчитываются до выбора "
                "исторического метода оценки запасов.",
            ],
        )

    def summary(self, period: SalesAnalyticsPeriod) -> SalesSummaryRead:
        """Calculate revenue, receipt count, average receipt and sold units in SQL."""
        revenue, receipt_count = self._session.execute(
            select(func.coalesce(func.sum(Sale.total_amount), 0), func.count(Sale.id)).where(
                *self._sale_filters(period)
            )
        ).one()
        source_rows = self._session.execute(
            select(
                SaleItem.source,
                func.coalesce(func.sum(SaleItem.fiscal_line_total), 0),
                func.coalesce(func.sum(SaleItem.quantity), 0),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(*self._sale_filters(period))
            .group_by(SaleItem.source)
        ).all()
        source_metrics = {
            source: (self._money(source_revenue), int(source_units))
            for source, source_revenue, source_units in source_rows
        }
        catalog_revenue, catalog_units = source_metrics.get(
            SaleItemSource.CATALOG, (ZERO_MONEY, 0)
        )
        free_sale_revenue, free_sale_units = source_metrics.get(
            SaleItemSource.MANUAL, (ZERO_MONEY, 0)
        )
        items_sold = catalog_units + free_sale_units
        revenue_money = self._money(revenue)
        receipt_total = int(receipt_count or 0)
        return SalesSummaryRead(
            period=period.read_model(),
            revenue=revenue_money,
            receipt_count=receipt_total,
            average_receipt=self._average(revenue_money, receipt_total),
            items_sold=items_sold,
            catalog_revenue=catalog_revenue,
            free_sale_revenue=free_sale_revenue,
            catalog_units=catalog_units,
            free_sale_units=free_sale_units,
            free_sale_revenue_share=self._share(free_sale_revenue, revenue_money),
            free_sale_units_share=self._share(free_sale_units, items_sold),
        )

    def timeline(self, period: SalesAnalyticsPeriod) -> list[SalesDailyRead]:
        """Group completed Sales and item quantities by business-local calendar date."""
        local_date = self._local_date_expression(Sale.completed_at, period)
        sale_rows = self._session.execute(
            select(
                local_date.label("business_date"),
                func.coalesce(func.sum(Sale.total_amount), 0),
                func.count(Sale.id),
            )
            .where(*self._sale_filters(period))
            .group_by(local_date)
            .order_by(local_date)
        ).all()
        item_rows = self._session.execute(
            select(
                local_date.label("business_date"),
                func.coalesce(func.sum(SaleItem.quantity), 0),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(*self._sale_filters(period))
            .group_by(local_date)
        ).all()
        sale_metrics = {
            self._coerce_date(day): (self._money(revenue), int(receipts))
            for day, revenue, receipts in sale_rows
        }
        item_metrics = {self._coerce_date(day): int(units) for day, units in item_rows}
        result: list[SalesDailyRead] = []
        current = period.start_date
        while current <= period.end_date:
            revenue, receipts = sale_metrics.get(current, (ZERO_MONEY, 0))
            result.append(
                SalesDailyRead(
                    date=current,
                    revenue=revenue,
                    receipt_count=receipts,
                    items_sold=item_metrics.get(current, 0),
                    average_receipt=self._average(revenue, receipts),
                )
            )
            current += timedelta(days=1)
        return result

    def top_products(
        self,
        period: SalesAnalyticsPeriod,
        *,
        limit: int = 10,
        ranking: SalesRanking = SalesRanking.REVENUE,
    ) -> list[SalesProductRead]:
        """Aggregate Catalog SaleItem snapshots into stable parent Product identities."""
        units = func.sum(SaleItem.quantity).label("units")
        revenue = func.sum(SaleItem.fiscal_line_total).label("revenue")
        statement = (
            select(
                CatalogVariant.product_id,
                func.max(SaleItem.product_title_snapshot),
                units,
                revenue,
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .join(CatalogVariant, SaleItem.variant_id == CatalogVariant.id)
            .where(
                *self._sale_filters(period),
                SaleItem.source == SaleItemSource.CATALOG,
            )
            .group_by(CatalogVariant.product_id)
            .order_by((units if ranking is SalesRanking.UNITS else revenue).desc(), units.desc())
            .limit(limit)
        )
        return [
            SalesProductRead(
                product_id=product_id,
                product_title=title or "Товар без названия",
                units=int(product_units),
                revenue=self._money(product_revenue),
            )
            for product_id, title, product_units, product_revenue in self._session.execute(
                statement
            )
        ]

    def top_variants(
        self,
        period: SalesAnalyticsPeriod,
        *,
        limit: int = 10,
        ranking: SalesRanking = SalesRanking.REVENUE,
    ) -> list[SalesVariantRead]:
        """Rank preserved Variant identities without collapsing color, size or type."""
        units = func.sum(SaleItem.quantity).label("units")
        revenue = func.sum(SaleItem.fiscal_line_total).label("revenue")
        statement = (
            select(
                SaleItem.variant_id,
                func.max(SaleItem.product_title_snapshot),
                func.max(SaleItem.variant_title_snapshot),
                func.max(SaleItem.display_label_snapshot),
                func.max(SaleItem.sku_snapshot),
                units,
                revenue,
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(
                *self._sale_filters(period),
                SaleItem.source == SaleItemSource.CATALOG,
                SaleItem.variant_id.is_not(None),
            )
            .group_by(SaleItem.variant_id)
            .order_by((units if ranking is SalesRanking.UNITS else revenue).desc(), units.desc())
            .limit(limit)
        )
        return [
            SalesVariantRead(
                variant_id=variant_id,
                product_title=product_title or "Товар без названия",
                variant_title=variant_title or "Основной вариант",
                display_label=display_label,
                sku=sku,
                units=int(variant_units),
                revenue=self._money(variant_revenue),
            )
            for (
                variant_id,
                product_title,
                variant_title,
                display_label,
                sku,
                variant_units,
                variant_revenue,
            ) in self._session.execute(statement)
        ]

    def top_free_sale_items(
        self,
        period: SalesAnalyticsPeriod,
        *,
        limit: int = 10,
        ranking: SalesRanking = SalesRanking.REVENUE,
    ) -> list[SalesFreeSaleItemRead]:
        """Group manual lines by a conservative name key while retaining actual revenue."""
        rows = self._session.execute(
            select(
                SaleItem.display_label_snapshot,
                func.coalesce(func.sum(SaleItem.quantity), 0),
                func.coalesce(func.sum(SaleItem.fiscal_line_total), 0),
                func.max(Sale.completed_at),
            )
            .join(Sale, SaleItem.sale_id == Sale.id)
            .where(
                *self._sale_filters(period),
                SaleItem.source == SaleItemSource.MANUAL,
            )
            .group_by(SaleItem.display_label_snapshot)
        ).all()
        grouped: dict[str, dict[str, object]] = {}
        for display_name, units, revenue, latest_at in rows:
            normalized_name = normalize_free_sale_name(display_name)
            if not normalized_name:
                continue
            existing = grouped.setdefault(
                normalized_name,
                {
                    "display_name": display_name,
                    "display_order": (latest_at, display_name),
                    "units": 0,
                    "revenue": ZERO_MONEY,
                },
            )
            display_order = (latest_at, display_name)
            if display_order > existing["display_order"]:
                existing["display_name"] = display_name
                existing["display_order"] = display_order
            existing["units"] = int(existing["units"]) + int(units)
            existing["revenue"] = self._money(existing["revenue"]) + self._money(revenue)
        result = [
            SalesFreeSaleItemRead(
                normalized_name=normalized_name,
                display_name=str(metrics["display_name"]),
                units=int(metrics["units"]),
                revenue=self._money(metrics["revenue"]),
            )
            for normalized_name, metrics in grouped.items()
        ]
        result.sort(
            key=lambda item: (
                item.units if ranking is SalesRanking.UNITS else item.revenue,
                item.revenue,
                item.normalized_name,
            ),
            reverse=True,
        )
        return result[:limit]

    def weekdays(
        self,
        period: SalesAnalyticsPeriod,
        *,
        daily: Sequence[SalesDailyRead] | None = None,
    ) -> list[SalesWeekdayRead]:
        """Aggregate exact daily results by weekday, including zero-sales observed days."""
        days = daily if daily is not None else self.timeline(period)
        result: list[SalesWeekdayRead] = []
        for weekday, label in enumerate(WEEKDAY_LABELS, start=1):
            observed = [item for item in days if item.date.isoweekday() == weekday]
            revenue = sum((item.revenue for item in observed), start=ZERO_MONEY)
            receipts = sum(item.receipt_count for item in observed)
            result.append(
                SalesWeekdayRead(
                    weekday=weekday,
                    label=label,
                    observed_days=len(observed),
                    revenue=self._money(revenue),
                    average_revenue=self._average(revenue, len(observed)),
                    receipt_count=receipts,
                    average_receipt=self._average(revenue, receipts),
                )
            )
        return result

    def payment_methods(self, period: SalesAnalyticsPeriod) -> list[SalesPaymentMethodRead]:
        """Summarize the single successful durable payment recorded for each completed Sale."""
        rows = self._session.execute(
            select(
                PaymentAttempt.payment_method,
                func.coalesce(func.sum(Sale.total_amount), 0),
                func.count(Sale.id),
            )
            .join(Sale, PaymentAttempt.sale_id == Sale.id)
            .where(
                *self._sale_filters(period),
                PaymentAttempt.status == PaymentStatus.SUCCEEDED,
            )
            .group_by(PaymentAttempt.payment_method)
            .order_by(func.sum(Sale.total_amount).desc())
        ).all()
        labels = {"cash": "Наличные", "card": "Карта / QR"}
        return [
            SalesPaymentMethodRead(
                payment_method=method.value,
                label=labels.get(method.value, method.value),
                revenue=self._money(revenue),
                receipt_count=int(receipts),
            )
            for method, revenue, receipts in rows
        ]

    @staticmethod
    def _sale_filters(period: SalesAnalyticsPeriod) -> tuple[object, ...]:
        """Return the one canonical completed-real-sale predicate."""
        return (
            Sale.status == SaleStatus.COMPLETED,
            Sale.completed_at.is_not(None),
            Sale.completed_at >= period.start_at,
            Sale.completed_at < period.end_at,
            Sale.deleted_at.is_(None),
        )

    def _local_date_expression(self, timestamp: object, period: SalesAnalyticsPeriod) -> object:
        """Build a database-side local-date expression for PostgreSQL and test SQLite."""
        bind = self._session.get_bind()
        dialect = bind.dialect.name
        if dialect == "postgresql":
            return cast(func.timezone(self._timezone_name, timestamp), Date)
        if dialect == "sqlite":
            zone = ZoneInfo(self._timezone_name)
            offset = datetime.combine(period.start_date, time.min, tzinfo=zone).utcoffset()
            minutes = int((offset or timedelta()).total_seconds() // 60)
            return func.date(timestamp, f"{minutes:+d} minutes")
        return cast(timestamp, Date)

    @staticmethod
    def _coerce_date(value: date | str) -> date:
        """Normalize driver-specific SQL date results."""
        return value if isinstance(value, date) else date.fromisoformat(value)

    @staticmethod
    def _money(value: object) -> Decimal:
        """Normalize aggregate output to Core's two-decimal money precision."""
        return Decimal(str(value or 0)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    @classmethod
    def _average(cls, total: Decimal, count: int) -> Decimal:
        """Return a safe two-decimal average, including a stable zero for empty sets."""
        if count == 0:
            return ZERO_MONEY
        return cls._money(total / count)

    @staticmethod
    def _share(part: Decimal | int, total: Decimal | int) -> Decimal:
        """Return a stable four-decimal ratio for source-share presentation."""
        if total == 0:
            return Decimal("0.0000")
        return (Decimal(part) / Decimal(total)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

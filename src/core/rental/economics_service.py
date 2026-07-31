from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogVariant
from core.rental.economics_schemas import (
    EfficiencyFlag,
    RentalAssetEconomicsRead,
    RentalProductEconomicsRead,
    RentalVariantEconomicsRead,
)
from core.rental.enums import AssetPurpose, RentalAvailability
from core.rental.models import (
    RentalAssetRecord,
    RentalMaintenanceRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.order_enums import RentalOrderItemStatus
from core.shared.db import UUIDv7
from core.shared.money import quantize_money

ZERO = Decimal("0.00")


class RentalEconomicsNotFoundError(Exception):
    """Raised when an economics projection target does not exist."""


class RentalEconomicsService:
    """Derive rental economics from immutable operational facts."""

    long_idle_after = timedelta(days=90)

    def __init__(self, session: Session, *, now: datetime | None = None) -> None:
        """Create a read service with an injectable business-time boundary."""
        self._session = session
        self._now = now or datetime.now(UTC)

    def get_asset(self, asset_id: UUIDv7) -> RentalAssetEconomicsRead:
        """Return economics calculated from this asset's completed operations."""
        asset = self._session.get(RentalAssetRecord, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise RentalEconomicsNotFoundError
        return self._asset_projection(asset)

    def get_variant(self, variant_id: UUIDv7) -> RentalVariantEconomicsRead:
        """Aggregate current asset economics for one Variant."""
        assets = list(
            self._session.scalars(
                select(RentalAssetRecord).where(
                    RentalAssetRecord.variant_id == variant_id,
                    RentalAssetRecord.deleted_at.is_(None),
                )
            ).all()
        )
        if self._session.get(CatalogVariant, variant_id) is None:
            raise RentalEconomicsNotFoundError
        economics = [self._asset_projection(asset) for asset in assets]
        lifetime_days = [
            max(
                Decimal(
                    str((self._now - self._aware(asset.created_at)).total_seconds() / 86400)
                ),
                Decimal("1"),
            )
            for asset in assets
        ]
        utilizations = [
            min(row.total_rental_days / days, Decimal("1"))
            for row, days in zip(economics, lifetime_days, strict=True)
        ]
        return RentalVariantEconomicsRead(
            variant_id=variant_id,
            asset_count=len(assets),
            active_asset_count=sum(asset.purpose is AssetPurpose.RENTAL for asset in assets),
            available_asset_count=sum(
                asset.availability is RentalAvailability.AVAILABLE for asset in assets
            ),
            revenue=self._sum(row.revenue for row in economics),
            expenses=self._sum(row.expenses for row in economics),
            profit=self._sum(row.net_income for row in economics),
            rental_count=sum(row.rental_count for row in economics),
            average_utilization=(sum(utilizations, ZERO) / len(utilizations)).quantize(
                Decimal("0.0001")
            )
            if utilizations
            else Decimal("0.0000"),
        )

    def get_product(self, product_id: UUIDv7) -> RentalProductEconomicsRead:
        """Aggregate current Variant economics for one Product."""
        variant_ids = list(
            self._session.scalars(
                select(CatalogVariant.id).where(
                    CatalogVariant.product_id == product_id,
                    CatalogVariant.deleted_at.is_(None),
                )
            ).all()
        )
        rows = [self.get_variant(variant_id) for variant_id in variant_ids]
        return RentalProductEconomicsRead(
            product_id=product_id,
            asset_count=sum(row.asset_count for row in rows),
            revenue=self._sum(row.revenue for row in rows),
            expenses=self._sum(row.expenses for row in rows),
            profit=self._sum(row.profit for row in rows),
            rental_count=sum(row.rental_count for row in rows),
        )

    def get_assets(self, asset_ids: list[UUIDv7]) -> dict[UUIDv7, RentalAssetEconomicsRead]:
        """Build projections for a bounded asset collection used by catalog reads."""
        if not asset_ids:
            return {}
        assets = self._session.scalars(
            select(RentalAssetRecord).where(RentalAssetRecord.id.in_(asset_ids))
        ).all()
        return {asset.id: self._asset_projection(asset) for asset in assets}

    def _asset_projection(self, asset: RentalAssetRecord) -> RentalAssetEconomicsRead:
        rows = self._session.execute(
            select(RentalOrderItemRecord, RentalOrderRecord)
            .join(RentalOrderRecord, RentalOrderRecord.id == RentalOrderItemRecord.order_id)
            .where(
                RentalOrderItemRecord.rental_asset_id == asset.id,
                RentalOrderItemRecord.status.in_(
                    (RentalOrderItemStatus.RETURNED, RentalOrderItemStatus.LOST)
                ),
                RentalOrderItemRecord.charged_amount.is_not(None),
            )
            .order_by(RentalOrderItemRecord.returned_at)
        ).all()
        maintenance = list(
            self._session.scalars(
                select(RentalMaintenanceRecord).where(
                    RentalMaintenanceRecord.rental_asset_id == asset.id
                )
            ).all()
        )
        revenue = self._sum(item.charged_amount or ZERO for item, _ in rows)
        expenses = self._sum(record.cost for record in maintenance)
        durations = [
            max((item.returned_at - order.issued_at).total_seconds(), 0)
            for item, order in rows
            if item.returned_at and order.issued_at
        ]
        last_rental_at = max(
            (order.issued_at for _, order in rows if order.issued_at), default=None
        )
        payback_at = self._payback_at(asset.acquisition_cost, rows, maintenance)
        flags: list[EfficiencyFlag] = []
        if not rows:
            flags.append(EfficiencyFlag.NEVER_RENTED)
        if asset.acquisition_cost is not None and revenue - expenses >= asset.acquisition_cost:
            flags.append(EfficiencyFlag.PAID_BACK)
        elif asset.acquisition_cost is not None:
            flags.append(EfficiencyFlag.NOT_PAID_BACK)
        if expenses > ZERO and (revenue == ZERO or expenses >= revenue * Decimal("0.5")):
            flags.append(EfficiencyFlag.HIGH_EXPENSES)
        if (
            last_rental_at is not None
            and self._now - self._aware(last_rental_at) >= self.long_idle_after
        ):
            flags.append(EfficiencyFlag.LONG_IDLE)
        return RentalAssetEconomicsRead(
            asset_id=asset.id,
            acquisition_cost=asset.acquisition_cost,
            rental_count=len(rows),
            total_rental_days=(Decimal(sum(durations)) / Decimal(86400)).quantize(Decimal("0.01")),
            revenue=revenue,
            average_rental_revenue=quantize_money(revenue / len(rows)) if rows else ZERO,
            expenses=expenses,
            net_income=quantize_money(revenue - expenses),
            last_rental_at=last_rental_at,
            payback_at=payback_at,
            flags=flags,
        )

    def _payback_at(
        self,
        acquisition_cost: Decimal | None,
        rows: Sequence[tuple[RentalOrderItemRecord, RentalOrderRecord]],
        maintenance: Sequence[RentalMaintenanceRecord],
    ) -> datetime | None:
        if acquisition_cost is None:
            return None
        events = [
            (item.returned_at, item.charged_amount or ZERO) for item, _ in rows if item.returned_at
        ]
        events += [(record.performed_at, -record.cost) for record in maintenance]
        balance = ZERO
        for occurred_at, amount in sorted(events, key=lambda event: event[0]):
            balance += amount
            if balance >= acquisition_cost:
                return occurred_at
        return None

    @staticmethod
    def _sum(values: Iterable[Decimal]) -> Decimal:
        return quantize_money(sum(values, ZERO))

    @staticmethod
    def _aware(value: datetime) -> datetime:
        """Normalize SQLite's timezone-naive test values to UTC."""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

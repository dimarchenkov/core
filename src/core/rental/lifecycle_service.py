from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.customers.models import CustomerRecord
from core.identity.models import User
from core.media.models import Image
from core.media.service import ImageService
from core.rental.enums import AssetPurpose
from core.rental.lifecycle_schemas import (
    AssetTimelineEvent,
    ConditionPhotoRead,
    ConditionPhotoStage,
    CustomerRentalHistoryItem,
    CustomerRentalHistoryRead,
    DamageCreate,
    DamageRead,
    MaintenanceCreate,
    MaintenanceRead,
    RentalAssetPassportRead,
)
from core.rental.models import (
    RentalAssetRecord,
    RentalConditionPhotoRecord,
    RentalDamageRecord,
    RentalMaintenanceRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.shared.db import UUIDv7


class RentalLifecycleNotFoundError(Exception):
    """Raised when a lifecycle operation references unavailable data."""


class RentalLifecycleReferenceError(Exception):
    """Raised when an order item does not belong to the selected asset."""


class RentalLifecycleService:
    """Append journals and derive physical-asset and customer rental history."""

    def __init__(self, session: Session) -> None:
        """Create a lifecycle service from one request-scoped session."""
        self._session = session

    def get_asset_passport(self, asset_id: UUIDv7) -> RentalAssetPassportRead:
        """Build one chronological passport without duplicating order facts."""
        catalog_row = self._session.execute(
            select(RentalAssetRecord, CatalogVariant, CatalogProduct)
            .join(CatalogVariant, CatalogVariant.id == RentalAssetRecord.variant_id)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(
                RentalAssetRecord.id == asset_id,
                RentalAssetRecord.deleted_at.is_(None),
            )
        ).one_or_none()
        if catalog_row is None:
            raise RentalLifecycleNotFoundError
        asset, variant, product = catalog_row
        order_rows = self._session.execute(
            select(RentalOrderItemRecord, RentalOrderRecord)
            .join(RentalOrderRecord, RentalOrderRecord.id == RentalOrderItemRecord.order_id)
            .where(RentalOrderItemRecord.rental_asset_id == asset_id)
            .order_by(RentalOrderRecord.created_at, RentalOrderItemRecord.created_at)
        ).all()
        maintenance_records = list(
            self._session.scalars(
                select(RentalMaintenanceRecord)
                .where(RentalMaintenanceRecord.rental_asset_id == asset_id)
                .order_by(RentalMaintenanceRecord.performed_at)
            ).all()
        )
        damage_records = list(
            self._session.scalars(
                select(RentalDamageRecord)
                .where(RentalDamageRecord.rental_asset_id == asset_id)
                .order_by(RentalDamageRecord.created_at)
            ).all()
        )
        photo_records = list(
            self._session.scalars(
                select(RentalConditionPhotoRecord)
                .where(RentalConditionPhotoRecord.rental_asset_id == asset_id)
                .order_by(RentalConditionPhotoRecord.created_at)
            ).all()
        )
        user_ids = {
            identifier
            for identifier in [
                *(record.performer_id for record in maintenance_records),
                *(record.recorded_by_id for record in damage_records),
            ]
            if identifier is not None
        }
        users = (
            {
                user.id: user.full_name
                for user in self._session.scalars(select(User).where(User.id.in_(user_ids))).all()
            }
            if user_ids
            else {}
        )
        order_by_item_id = {item.id: order for item, order in order_rows}
        maintenance = [
            MaintenanceRead(
                id=record.id,
                performed_at=record.performed_at,
                service_type=record.service_type,
                comment=record.comment,
                performer_id=record.performer_id,
                performer_name=users.get(record.performer_id),
                result=record.result,
            )
            for record in maintenance_records
        ]
        damages = [
            DamageRead(
                id=record.id,
                order_item_id=record.order_item_id,
                order_id=(
                    order.id if (order := order_by_item_id.get(record.order_item_id)) else None
                ),
                order_number=order.order_number if order is not None else None,
                description=record.description,
                comment=record.comment,
                severity=record.severity,
                recorded_by_id=record.recorded_by_id,
                recorded_by_name=users.get(record.recorded_by_id),
                created_at=record.created_at,
            )
            for record in damage_records
        ]
        timeline = self._timeline(asset, order_rows, maintenance, damages)
        current_order = next(
            (
                order
                for item, order in order_rows
                if item.status is RentalOrderItemStatus.ISSUED
                and order.status is RentalOrderStatus.ISSUED
            ),
            None,
        )
        rental_count = sum(order.issued_at is not None for _, order in order_rows)
        completed_rental_count = sum(
            item.status in {RentalOrderItemStatus.RETURNED, RentalOrderItemStatus.LOST}
            for item, _ in order_rows
        )
        return RentalAssetPassportRead(
            id=asset.id,
            asset_number=asset.asset_number,
            product_id=product.id,
            product_title=product.title,
            variant_id=variant.id,
            variant_title=variant.title,
            sku=variant.sku,
            purpose=asset.purpose,
            condition=asset.condition,
            availability=asset.availability,
            retirement_reason=asset.retirement_reason,
            created_at=asset.created_at,
            rental_count=rental_count,
            completed_rental_count=completed_rental_count,
            current_order_id=current_order.id if current_order is not None else None,
            current_order_number=current_order.order_number if current_order is not None else None,
            timeline=timeline,
            maintenance=maintenance,
            damages=damages,
            condition_photos=[
                ConditionPhotoRead(
                    id=record.id,
                    image_id=record.image_id,
                    order_item_id=record.order_item_id,
                    stage=record.stage,
                    recorded_by_id=record.recorded_by_id,
                    created_at=record.created_at,
                )
                for record in photo_records
            ],
        )

    def add_maintenance(
        self,
        asset_id: UUIDv7,
        data: MaintenanceCreate,
        *,
        actor_id: UUIDv7,
    ) -> MaintenanceRead:
        """Append one service journal entry without changing the RentalAsset aggregate."""
        self._require_asset(asset_id)
        record = RentalMaintenanceRecord(
            rental_asset_id=asset_id,
            performed_at=data.performed_at or datetime.now(UTC),
            service_type=data.service_type.value,
            comment=self._optional_text(data.comment),
            performer_id=actor_id,
            result=data.result,
            created_by_id=actor_id,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        user = self._session.get(User, actor_id)
        return MaintenanceRead(
            id=record.id,
            performed_at=record.performed_at,
            service_type=record.service_type,
            comment=record.comment,
            performer_id=record.performer_id,
            performer_name=user.full_name if user is not None else None,
            result=record.result,
        )

    def add_damage(
        self,
        asset_id: UUIDv7,
        data: DamageCreate,
        *,
        actor_id: UUIDv7,
    ) -> DamageRead:
        """Append one damage observation without changing return workflow semantics."""
        self._require_asset(asset_id)
        order: RentalOrderRecord | None = None
        if data.order_item_id is not None:
            item = self._session.get(RentalOrderItemRecord, data.order_item_id)
            if item is None or item.rental_asset_id != asset_id:
                raise RentalLifecycleReferenceError
            order = self._session.get(RentalOrderRecord, item.order_id)
        record = RentalDamageRecord(
            rental_asset_id=asset_id,
            order_item_id=data.order_item_id,
            description=data.description.strip(),
            comment=self._optional_text(data.comment),
            severity=data.severity.value,
            recorded_by_id=actor_id,
            created_by_id=actor_id,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        user = self._session.get(User, actor_id)
        return DamageRead(
            id=record.id,
            order_item_id=record.order_item_id,
            order_id=order.id if order is not None else None,
            order_number=order.order_number if order is not None else None,
            description=record.description,
            comment=record.comment,
            severity=record.severity,
            recorded_by_id=record.recorded_by_id,
            recorded_by_name=user.full_name if user is not None else None,
            created_at=record.created_at,
        )

    def add_condition_photo(
        self,
        asset_id: UUIDv7,
        *,
        stage: ConditionPhotoStage,
        original_filename: str,
        content: bytes,
        image_service: ImageService,
        actor_id: UUIDv7,
        order_item_id: UUIDv7 | None = None,
    ) -> ConditionPhotoRead:
        """Store source evidence and its Rental-owned link in one transaction."""
        self._require_asset(asset_id)
        if order_item_id is not None:
            item = self._session.get(RentalOrderItemRecord, order_item_id)
            if item is None or item.rental_asset_id != asset_id:
                raise RentalLifecycleReferenceError
        image: Image | None = None
        committed = False
        try:
            image = image_service.upload_source_image(
                original_filename,
                content,
                actor_id=actor_id,
            )
            record = RentalConditionPhotoRecord(
                rental_asset_id=asset_id,
                order_item_id=order_item_id,
                image_id=image.id,
                stage=stage.value,
                recorded_by_id=actor_id,
                created_by_id=actor_id,
            )
            self._session.add(record)
            self._session.commit()
            committed = True
            self._session.refresh(record)
            return ConditionPhotoRead(
                id=record.id,
                image_id=record.image_id,
                order_item_id=record.order_item_id,
                stage=record.stage,
                recorded_by_id=record.recorded_by_id,
                created_at=record.created_at,
            )
        except Exception:
            self._session.rollback()
            if image is not None and not committed:
                image_service.discard_uncommitted_source(image)
            raise

    def get_customer_history(self, customer_id: UUIDv7) -> CustomerRentalHistoryRead:
        """Return active and completed contracts without changing Customers ownership."""
        customer = self._session.get(CustomerRecord, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise RentalLifecycleNotFoundError
        orders = list(
            self._session.scalars(
                select(RentalOrderRecord)
                .where(
                    RentalOrderRecord.customer_id == customer_id,
                    RentalOrderRecord.deleted_at.is_(None),
                    RentalOrderRecord.status.in_(
                        [RentalOrderStatus.ISSUED, RentalOrderStatus.CLOSED]
                    ),
                )
                .order_by(RentalOrderRecord.created_at.desc())
            ).all()
        )
        rows = [
            CustomerRentalHistoryItem(
                order_id=order.id,
                order_number=order.order_number,
                status=order.status,
                planned_start_at=order.planned_start_at,
                planned_return_at=order.planned_return_at,
                issued_at=order.issued_at,
                closed_at=order.closed_at,
                item_count=len(order.items),
            )
            for order in orders
        ]
        return CustomerRentalHistoryRead(
            customer_id=customer.id,
            customer_number=customer.customer_number,
            full_name=customer.full_name,
            phone=customer.phone,
            status=customer.status,
            rental_count=len(rows),
            active_rentals=[row for row in rows if row.status is RentalOrderStatus.ISSUED],
            completed_rentals=[row for row in rows if row.status is RentalOrderStatus.CLOSED],
            current_debt_amount=None,
        )

    def _require_asset(self, asset_id: UUIDv7) -> RentalAssetRecord:
        asset = self._session.get(RentalAssetRecord, asset_id)
        if asset is None or asset.deleted_at is not None:
            raise RentalLifecycleNotFoundError
        return asset

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @staticmethod
    def _timeline(
        asset: RentalAssetRecord,
        order_rows: list[tuple[RentalOrderItemRecord, RentalOrderRecord]],
        maintenance: list[MaintenanceRead],
        damages: list[DamageRead],
    ) -> list[AssetTimelineEvent]:
        events = [
            AssetTimelineEvent(
                event_type="intake",
                occurred_at=asset.created_at,
                title="Поступление",
                detail="Предмет создан при завершении приёмки.",
            )
        ]
        for item, order in order_rows:
            if order.issued_at is not None:
                events.append(
                    AssetTimelineEvent(
                        event_type="issued",
                        occurred_at=order.issued_at,
                        title="Выдан в аренду",
                        detail=item.title_snapshot,
                        order_id=order.id,
                        order_number=order.order_number,
                        customer_id=order.customer_id,
                        customer_name=order.customer_name_snapshot,
                    )
                )
            if item.returned_at is not None:
                events.append(
                    AssetTimelineEvent(
                        event_type=item.status.value,
                        occurred_at=item.returned_at,
                        title=(
                            "Возвращён"
                            if item.status is RentalOrderItemStatus.RETURNED
                            else "Утрачен"
                        ),
                        detail=item.return_note,
                        order_id=order.id,
                        order_number=order.order_number,
                        customer_id=order.customer_id,
                        customer_name=order.customer_name_snapshot,
                    )
                )
        events.extend(
            AssetTimelineEvent(
                event_type="maintenance",
                occurred_at=record.performed_at,
                title="Обслуживание",
                detail=f"{record.service_type}: {record.result}",
            )
            for record in maintenance
        )
        events.extend(
            AssetTimelineEvent(
                event_type="damage",
                occurred_at=record.created_at,
                title="Зафиксировано повреждение",
                detail=record.description,
                order_id=record.order_id,
                order_number=record.order_number,
            )
            for record in damages
        )
        if asset.purpose is AssetPurpose.RETIRED:
            events.append(
                AssetTimelineEvent(
                    event_type="retired",
                    occurred_at=asset.updated_at,
                    title="Списан",
                    detail=asset.retirement_reason,
                )
            )
        return sorted(events, key=lambda event: event.occurred_at)

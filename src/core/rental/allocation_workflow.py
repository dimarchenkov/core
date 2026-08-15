from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogVariant
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.inventory.service import InventoryService
from core.rental.allocation_schemas import InventoryAdjustmentReason
from core.rental.asset import RentalAsset
from core.rental.enums import AssetPurpose
from core.rental.mapper import rental_asset_from_record, update_rental_asset_record
from core.rental.models import RentalAssetRecord
from core.rental.repository import RentalAssetRepository
from core.rental.service import RentalAssetService
from core.shared.db import UUIDv7, generate_uuid_v7


class RentalAllocationError(Exception):
    """Raised when inventory cannot be allocated or an asset cannot be withdrawn."""


class InventoryAdjustmentForbiddenError(Exception):
    """Raised when a non-administrator requests a manual correction."""


class InventoryAdjustmentBalanceError(Exception):
    """Raised when a correction would undercut allocated physical units."""


class InventoryRentalWorkflow:
    """Coordinate Inventory facts and RentalAsset lifecycle in one transaction."""

    def __init__(self, session: Session) -> None:
        """Create a transaction coordinator for one request-scoped session."""
        self._session = session
        self._inventory = InventoryService(session)
        self._assets = RentalAssetRepository(session)

    def allocate(self, variant_id: UUIDv7, quantity: int, *, actor_id: UUIDv7) -> list[RentalAsset]:
        """Atomically turn ordinary physical units into numbered rental assets."""
        try:
            self._lock_variant(variant_id)
            ordinary = self._ordinary_quantity(variant_id)
            if quantity <= 0 or Decimal(quantity) > ordinary:
                raise RentalAllocationError
            assets = RentalAssetService(self._session).create_from_intake(
                variant_id=variant_id,
                intake_item_id=None,
                quantity=quantity,
                actor_id=actor_id,
            )
            self._session.commit()
            return assets
        except Exception:
            self._session.rollback()
            raise

    def withdraw(self, asset_id: UUIDv7, *, actor_id: UUIDv7) -> RentalAsset:
        """Return one eligible rental asset to ordinary inventory availability."""
        try:
            record = self._assets.get_for_update(asset_id)
            if record is None:
                raise RentalAllocationError
            asset = rental_asset_from_record(record)
            asset.withdraw_for_sale()
            update_rental_asset_record(record, asset)
            record.updated_by_id = actor_id
            self._session.commit()
            return asset
        except Exception:
            self._session.rollback()
            raise

    def adjust(
        self,
        variant_id: UUIDv7,
        quantity_delta: Decimal,
        reason: InventoryAdjustmentReason,
        comment: str | None,
        *,
        actor_id: UUIDv7,
        is_admin: bool,
    ) -> tuple[StockMovement, Decimal]:
        """Append an administrator-attributed inventory correction."""
        if not is_admin:
            raise InventoryAdjustmentForbiddenError
        try:
            self._lock_variant(variant_id)
            resulting_balance = self._inventory.get_balance(variant_id) + quantity_delta
            reserved = self._reserved_count(variant_id)
            if quantity_delta == 0 or resulting_balance < Decimal(reserved):
                raise InventoryAdjustmentBalanceError
            source_id = generate_uuid_v7()
            notes = f"{reason.value}: {(comment or '').strip()}".rstrip(": ")
            movement = self._inventory.create_movement(
                variant_id,
                MovementType.ADJUSTMENT,
                quantity_delta,
                SourceType.INVENTORY,
                source_id,
                notes=notes,
                actor_id=actor_id,
            )
            self._session.commit()
            return movement, resulting_balance
        except Exception:
            self._session.rollback()
            raise

    def _lock_variant(self, variant_id: UUIDv7) -> None:
        variant = self._session.scalar(
            select(CatalogVariant)
            .where(
                CatalogVariant.id == variant_id,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
            )
            .with_for_update()
        )
        if variant is None:
            raise RentalAllocationError

    def _reserved_count(self, variant_id: UUIDv7) -> int:
        return int(
            self._session.scalar(
                select(func.count(RentalAssetRecord.id)).where(
                    RentalAssetRecord.variant_id == variant_id,
                    RentalAssetRecord.purpose != AssetPurpose.SALE,
                    RentalAssetRecord.deleted_at.is_(None),
                )
            )
            or 0
        )

    def _ordinary_quantity(self, variant_id: UUIDv7) -> Decimal:
        return self._inventory.get_balance(variant_id) - Decimal(self._reserved_count(variant_id))

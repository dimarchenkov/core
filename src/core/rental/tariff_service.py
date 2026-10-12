from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogVariant
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.rental.enums import AssetPurpose
from core.rental.models import RentalAssetRecord
from core.rental.tariff_enums import RentalTariffStatus
from core.rental.tariff_models import RentalTariffRecord
from core.rental.tariff_repository import RentalTariffRepository
from core.rental.tariff_schemas import RentalTariffCreate, RentalTariffUpdate, RentalTariffWrite
from core.shared.db import UUIDv7, generate_uuid_v7
from core.shared.money import quantize_money


class RentalTariffNotFoundError(Exception):
    """Raised when a tariff version does not exist."""


class RentableVariantNotFoundError(Exception):
    """Raised when the owner is not an active Variant with a RentalAsset."""


class RentalTariffDeleteBlockedError(Exception):
    """Raised when history or business use makes hard deletion unsafe."""


class RentalTariffService:
    """Create, version, select, archive, and protect Rental tariff terms."""

    def __init__(self, session: Session) -> None:
        """Bind tariff operations to the caller-owned transaction."""
        self._session = session
        self._tariffs = RentalTariffRepository(session)

    def create(
        self,
        variant_id: UUIDv7,
        data: RentalTariffCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> RentalTariffRecord:
        """Create the first active version for one rentable CatalogVariant."""
        self._require_rentable_variant(variant_id)
        tariff = self._build_record(
            variant_id=variant_id,
            lineage_id=generate_uuid_v7(),
            version=1,
            data=data,
            actor_id=actor_id,
        )
        self._tariffs.add(tariff)
        self._session.flush()
        return tariff

    def create_version(
        self,
        tariff_id: UUIDv7,
        data: RentalTariffUpdate,
        *,
        actor_id: UUIDv7 | None = None,
        today: date | None = None,
    ) -> RentalTariffRecord:
        """Preserve the old row and append a complete new commercial version."""
        old = self._require_tariff(tariff_id)
        self._require_rentable_variant(old.variant_id)
        effective_today = today or datetime.now(UTC).date()
        now = datetime.now(UTC)
        if data.valid_from > effective_today and old.status is RentalTariffStatus.ACTIVE:
            proposed_end = data.valid_from - timedelta(days=1)
            if proposed_end >= old.valid_from:
                old.valid_to = proposed_end
            else:
                old.status = RentalTariffStatus.SUPERSEDED
                old.superseded_at = now
        else:
            old.status = RentalTariffStatus.SUPERSEDED
            old.superseded_at = now
        old.updated_by_id = actor_id

        new = self._build_record(
            variant_id=old.variant_id,
            lineage_id=old.lineage_id,
            version=self._tariffs.next_version(old.lineage_id),
            data=data,
            actor_id=actor_id,
        )
        self._tariffs.add(new)
        self._session.flush()
        return new

    def list_selectable(
        self, variant_id: UUIDv7, *, on_date: date | None = None
    ) -> Sequence[RentalTariffRecord]:
        """Canonical application query for a new Rental."""
        self._require_variant_exists(variant_id)
        return self._tariffs.list_selectable(
            variant_id, on_date=on_date or datetime.now(UTC).date()
        )

    def list_management(self, variant_id: UUIDv7) -> Sequence[RentalTariffRecord]:
        """Return operator-managed active schedules irrespective of today's date."""
        self._require_variant_exists(variant_id)
        return self._tariffs.list_management(variant_id)

    def history(self, tariff_id: UUIDv7) -> Sequence[RentalTariffRecord]:
        """Return every preserved version for the selected tariff line."""
        tariff = self._require_tariff(tariff_id)
        return self._tariffs.list_history(tariff.lineage_id)

    def archive(
        self, tariff_id: UUIDv7, *, actor_id: UUIDv7 | None = None
    ) -> RentalTariffRecord:
        """Remove a version from selectable schedules while preserving it."""
        tariff = self._require_tariff(tariff_id)
        tariff.status = RentalTariffStatus.ARCHIVED
        tariff.updated_by_id = actor_id
        self._session.flush()
        return tariff

    def mark_used(self, tariff_id: UUIDv7) -> RentalTariffRecord:
        """Lock an exact version when a future Rental snapshot first references it."""
        tariff = self._require_tariff(tariff_id)
        if tariff.locked_at is None:
            tariff.locked_at = datetime.now(UTC)
            self._session.flush()
        return tariff

    def hard_delete_unused(self, tariff_id: UUIDv7) -> None:
        """Delete only a never-used, single-version test tariff line."""
        tariff = self._require_tariff(tariff_id)
        lineage_count = self._session.scalar(
            select(func.count(RentalTariffRecord.id)).where(
                RentalTariffRecord.lineage_id == tariff.lineage_id
            )
        )
        if tariff.locked_at is not None or lineage_count != 1:
            raise RentalTariffDeleteBlockedError
        self._session.delete(tariff)
        self._session.flush()

    def _build_record(
        self,
        *,
        variant_id: UUIDv7,
        lineage_id: UUIDv7,
        version: int,
        data: RentalTariffWrite,
        actor_id: UUIDv7 | None,
    ) -> RentalTariffRecord:
        deposit = data.deposit_amount
        if deposit is None:
            default_deposit = PriceRepository(self._session).get_current(
                variant_id, PriceType.RENTAL_DEPOSIT, at=datetime.now(UTC)
            )
            deposit = default_deposit.amount if default_deposit is not None else None
        return RentalTariffRecord(
            variant_id=variant_id,
            lineage_id=lineage_id,
            version=version,
            name=data.name,
            type=data.type,
            price=quantize_money(data.price),
            duration_minutes=data.duration_minutes,
            slot_start=data.slot_start,
            slot_end=data.slot_end,
            grace_minutes=data.grace_minutes,
            deposit_amount=quantize_money(deposit) if deposit is not None else None,
            requires_agreement=data.requires_agreement,
            valid_from=data.valid_from,
            valid_to=data.valid_to,
            status=RentalTariffStatus.ACTIVE,
            created_by_id=actor_id,
        )

    def _require_tariff(self, tariff_id: UUIDv7) -> RentalTariffRecord:
        tariff = self._tariffs.get(tariff_id)
        if tariff is None:
            raise RentalTariffNotFoundError
        return tariff

    def _require_variant_exists(self, variant_id: UUIDv7) -> CatalogVariant:
        variant = self._session.get(CatalogVariant, variant_id)
        if variant is None or variant.deleted_at is not None:
            raise RentableVariantNotFoundError
        return variant

    def _require_rentable_variant(self, variant_id: UUIDv7) -> CatalogVariant:
        variant = self._require_variant_exists(variant_id)
        rental_assets = self._session.scalar(
            select(func.count(RentalAssetRecord.id)).where(
                RentalAssetRecord.variant_id == variant_id,
                RentalAssetRecord.purpose == AssetPurpose.RENTAL,
                RentalAssetRecord.deleted_at.is_(None),
            )
        )
        if not variant.is_active or not rental_assets:
            raise RentableVariantNotFoundError
        return variant

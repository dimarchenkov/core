from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.rental.tariff_enums import RentalTariffStatus
from core.rental.tariff_models import RentalTariffRecord
from core.shared.db import UUIDv7


class RentalTariffRepository:
    """Persistence queries for tariff versions and selectable schedules."""

    def __init__(self, session: Session) -> None:
        """Bind tariff queries to the current transaction."""
        self._session = session

    def add(self, tariff: RentalTariffRecord) -> RentalTariffRecord:
        """Stage a tariff version for persistence."""
        self._session.add(tariff)
        return tariff

    def get(self, tariff_id: UUIDv7) -> RentalTariffRecord | None:
        """Return one exact version by identifier."""
        return self._session.get(RentalTariffRecord, tariff_id)

    def list_selectable(
        self, variant_id: UUIDv7, *, on_date: date
    ) -> Sequence[RentalTariffRecord]:
        """Return the canonical tariffs available to a new Rental on one date."""
        return self._session.scalars(
            select(RentalTariffRecord)
            .where(
                RentalTariffRecord.variant_id == variant_id,
                RentalTariffRecord.status == RentalTariffStatus.ACTIVE,
                RentalTariffRecord.deleted_at.is_(None),
                RentalTariffRecord.valid_from <= on_date,
                or_(
                    RentalTariffRecord.valid_to.is_(None),
                    RentalTariffRecord.valid_to >= on_date,
                ),
            )
            .order_by(RentalTariffRecord.name, RentalTariffRecord.version.desc())
        ).all()

    def list_management(self, variant_id: UUIDv7) -> Sequence[RentalTariffRecord]:
        """Return active schedules, including future and naturally expired versions."""
        return self._session.scalars(
            select(RentalTariffRecord)
            .where(
                RentalTariffRecord.variant_id == variant_id,
                RentalTariffRecord.status == RentalTariffStatus.ACTIVE,
                RentalTariffRecord.deleted_at.is_(None),
            )
            .order_by(
                RentalTariffRecord.valid_from.desc(),
                RentalTariffRecord.name,
                RentalTariffRecord.version.desc(),
            )
        ).all()

    def list_history(self, lineage_id: UUIDv7) -> Sequence[RentalTariffRecord]:
        """Return one tariff lineage newest version first."""
        return self._session.scalars(
            select(RentalTariffRecord)
            .where(RentalTariffRecord.lineage_id == lineage_id)
            .order_by(RentalTariffRecord.version.desc())
        ).all()

    def next_version(self, lineage_id: UUIDv7) -> int:
        """Allocate the next monotonic version inside one lineage."""
        current = self._session.scalar(
            select(func.max(RentalTariffRecord.version)).where(
                RentalTariffRecord.lineage_id == lineage_id
            )
        )
        return int(current or 0) + 1

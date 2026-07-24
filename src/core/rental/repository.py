from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.rental.models import RentalAssetRecord
from core.shared.db import UUIDv7


class RentalAssetRepository:
    """Database access for persisted RentalAsset state projections."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the caller-owned transaction."""
        self._session = session

    def add(self, record: RentalAssetRecord) -> RentalAssetRecord:
        """Stage one new asset record without committing the transaction."""
        self._session.add(record)
        return record

    def get(self, asset_id: UUIDv7) -> RentalAssetRecord | None:
        """Return one non-deleted asset record by technical identifier."""
        statement = select(RentalAssetRecord).where(
            RentalAssetRecord.id == asset_id,
            RentalAssetRecord.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def list_for_intake_item(self, intake_item_id: UUIDv7) -> Sequence[RentalAssetRecord]:
        """Return assets created from one intake line in stable number order."""
        statement = (
            select(RentalAssetRecord)
            .where(
                RentalAssetRecord.intake_item_id == intake_item_id,
                RentalAssetRecord.deleted_at.is_(None),
            )
            .order_by(RentalAssetRecord.asset_number)
        )
        return self._session.scalars(statement).all()

    def next_asset_number(self) -> int:
        """Reserve the next asset number from PostgreSQL or the test database."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            return self._session.scalar(text("SELECT nextval('rental_asset_number_seq')"))

        statement = (
            select(RentalAssetRecord.asset_number)
            .order_by(RentalAssetRecord.asset_number.desc())
            .limit(1)
        )
        asset_number = self._session.scalar(statement)
        if asset_number is None:
            return 1
        return int(asset_number.removeprefix("RENT-")) + 1

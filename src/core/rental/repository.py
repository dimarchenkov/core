from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, selectinload

from core.rental.models import RentalAssetRecord, RentalOrderRecord
from core.rental.order_enums import RentalOrderStatus
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

    def get_for_update(self, asset_id: UUIDv7) -> RentalAssetRecord | None:
        """Lock one asset projection for a rental lifecycle workflow."""
        statement = (
            select(RentalAssetRecord)
            .where(
                RentalAssetRecord.id == asset_id,
                RentalAssetRecord.deleted_at.is_(None),
            )
            .with_for_update()
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


class RentalOrderRepository:
    """SQLAlchemy persistence for RentalOrder aggregate projections."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the caller-owned transaction."""
        self._session = session

    def add(self, record: RentalOrderRecord) -> RentalOrderRecord:
        """Stage a new aggregate projection without committing."""
        self._session.add(record)
        return record

    def get(self, order_id: UUIDv7, *, for_update: bool = False) -> RentalOrderRecord | None:
        """Return one aggregate projection with all owned items loaded."""
        statement = (
            select(RentalOrderRecord)
            .options(selectinload(RentalOrderRecord.items))
            .where(
                RentalOrderRecord.id == order_id,
                RentalOrderRecord.deleted_at.is_(None),
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_by_number(self, order_number: str) -> RentalOrderRecord | None:
        """Return one aggregate projection by stable business number."""
        statement = (
            select(RentalOrderRecord)
            .options(selectinload(RentalOrderRecord.items))
            .where(
                RentalOrderRecord.order_number == order_number,
                RentalOrderRecord.deleted_at.is_(None),
            )
        )
        return self._session.scalar(statement)

    def save(self, record: RentalOrderRecord) -> RentalOrderRecord:
        """Stage an existing aggregate projection without committing."""
        self._session.add(record)
        return record

    def search(
        self,
        query: str | None = None,
        *,
        status: RentalOrderStatus | None = None,
        customer_id: UUIDv7 | None = None,
    ) -> Sequence[RentalOrderRecord]:
        """Search orders by number or customer snapshots in newest-first order."""
        statement = select(RentalOrderRecord).options(selectinload(RentalOrderRecord.items)).where(
            RentalOrderRecord.deleted_at.is_(None)
        )
        if status is not None:
            statement = statement.where(RentalOrderRecord.status == status)
        if customer_id is not None:
            statement = statement.where(RentalOrderRecord.customer_id == customer_id)
        normalized_query = (query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    RentalOrderRecord.order_number.ilike(pattern),
                    RentalOrderRecord.customer_name_snapshot.ilike(pattern),
                    RentalOrderRecord.customer_phone_snapshot.ilike(pattern),
                )
            )
        statement = statement.order_by(
            RentalOrderRecord.created_at.desc(),
            RentalOrderRecord.order_number.desc(),
        )
        return self._session.scalars(statement).all()

    def reserve_next_order_number(self) -> int:
        """Reserve the next order number through PostgreSQL or SQLite fallback."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            number = self._session.scalar(text("SELECT nextval('rental_order_number_seq')"))
            return int(number)
        numbers = self._session.scalars(select(RentalOrderRecord.order_number)).all()
        return max((int(number.removeprefix("RORD-")) for number in numbers), default=0) + 1

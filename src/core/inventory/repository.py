from __future__ import annotations

from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.shared.db import UUIDv7


class StockMovementRepository:
    """Database access for immutable inventory ledger records."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current database session."""
        self._session = session

    def add(self, movement: StockMovement) -> StockMovement:
        """Add one newly-created movement to the current unit of work."""
        self._session.add(movement)
        return movement

    def get(self, movement_id: UUIDv7) -> StockMovement | None:
        """Return an immutable movement by its identifier."""
        return self._session.get(StockMovement, movement_id)

    def list_for_variant(self, variant_id: UUIDv7) -> Sequence[StockMovement]:
        """Return a variant's immutable movement history in creation order."""
        statement = (
            select(StockMovement)
            .where(StockMovement.variant_id == variant_id)
            .order_by(StockMovement.created_at, StockMovement.id)
        )
        return self._session.scalars(statement).all()

    def list_original_receipt_movements(self, receipt_id: UUIDv7) -> Sequence[StockMovement]:
        """Return original receipt movements for one receipt in deterministic ledger order."""
        statement = (
            select(StockMovement)
            .where(
                StockMovement.source_type == SourceType.RECEIPT,
                StockMovement.source_id == receipt_id,
                StockMovement.movement_type == MovementType.RECEIPT,
            )
            .order_by(StockMovement.created_at, StockMovement.id)
        )
        return self._session.scalars(statement).all()

    def get_balance(self, variant_id: UUIDv7) -> Decimal:
        """Calculate one variant balance in SQL without loading movement rows."""
        statement = select(func.sum(StockMovement.quantity_delta)).where(
            StockMovement.variant_id == variant_id
        )
        total = self._session.scalar(statement)
        return total if isinstance(total, Decimal) else Decimal("0")

    def get_balances(self, variant_ids: Collection[UUIDv7]) -> dict[UUIDv7, Decimal]:
        """Calculate requested variant balances with one grouped SQL aggregate query."""
        requested_ids = tuple(variant_ids)
        balances = {variant_id: Decimal("0") for variant_id in requested_ids}
        if not requested_ids:
            return balances

        statement = (
            select(StockMovement.variant_id, func.sum(StockMovement.quantity_delta))
            .where(StockMovement.variant_id.in_(requested_ids))
            .group_by(StockMovement.variant_id)
        )
        for variant_id, total in self._session.execute(statement):
            balances[variant_id] = total if isinstance(total, Decimal) else Decimal("0")
        return balances

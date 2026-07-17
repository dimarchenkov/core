from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

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

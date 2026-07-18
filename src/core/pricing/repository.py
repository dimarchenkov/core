from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import UUIDv7


class PriceRepository:
    """Database access for append-only variant price history."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current database session."""
        self._session = session

    def add(self, price: Price) -> Price:
        """Add one newly-created price fact to the current unit of work."""
        self._session.add(price)
        return price

    def get_current(
        self,
        variant_id: UUIDv7,
        price_type: PriceType,
        *,
        at: datetime,
    ) -> Price | None:
        """Return the newest applicable price with deterministic tie-breaking."""
        statement = (
            select(Price)
            .where(
                Price.variant_id == variant_id,
                Price.price_type == price_type,
                Price.effective_from <= at,
            )
            .order_by(
                Price.effective_from.desc(),
                Price.created_at.desc(),
                Price.id.desc(),
            )
            .limit(1)
        )
        return self._session.scalar(statement)

    def list_history(
        self,
        variant_id: UUIDv7,
        *,
        price_type: PriceType | None = None,
    ) -> Sequence[Price]:
        """Return immutable price history newest first, optionally filtered by type."""
        statement = select(Price).where(Price.variant_id == variant_id)
        if price_type is not None:
            statement = statement.where(Price.price_type == price_type)
        statement = statement.order_by(
            Price.effective_from.desc(),
            Price.created_at.desc(),
            Price.id.desc(),
        )
        return self._session.scalars(statement).all()

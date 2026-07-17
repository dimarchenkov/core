from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Numeric, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from core.inventory.enums import MovementType, SourceType
from core.shared.db import BaseModel, UUIDv7


def _movement_type_values(enum_class: type[MovementType]) -> list[str]:
    """Return database values for stock movement types."""
    return [movement_type.value for movement_type in enum_class]


def _source_type_values(enum_class: type[SourceType]) -> list[str]:
    """Return database values for stock movement sources."""
    return [source_type.value for source_type in enum_class]


class StockMovement(BaseModel):
    """Immutable ledger entry describing one quantity change for a catalog variant."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity_delta <> 0", name="ck_stock_movements_quantity_delta_nonzero"),
    )

    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    movement_type: Mapped[MovementType] = mapped_column(
        Enum(MovementType, name="movement_type", values_callable=_movement_type_values),
        nullable=False,
    )
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", values_callable=_source_type_values),
        nullable=False,
    )
    source_id: Mapped[UUIDv7] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because stock corrections require compensating movements."""
        del actor_id
        raise RuntimeError("Stock movements are immutable.")

    def restore(self) -> None:
        """Reject restoration because stock movements cannot be deleted."""
        raise RuntimeError("Stock movements are immutable.")

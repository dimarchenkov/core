from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.pricing.enums import PriceType
from core.shared.db import BaseModel, UUIDv7


def _price_type_values(enum_class: type[PriceType]) -> list[str]:
    """Return stable database values for price types."""
    return [price_type.value for price_type in enum_class]


class Price(BaseModel):
    """Immutable price fact for one sellable catalog variant."""

    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_prices_amount_nonnegative"),
        CheckConstraint("currency = 'RUB'", name="ck_prices_currency_rub"),
        Index("ix_prices_variant_type_effective", "variant_id", "price_type", "effective_from"),
    )

    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    price_type: Mapped[PriceType] = mapped_column(
        Enum(PriceType, name="price_type", values_callable=_price_type_values),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="RUB",
        server_default="RUB",
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because a correction must append a new price fact."""
        del actor_id
        raise RuntimeError("Prices are immutable.")

    def restore(self) -> None:
        """Reject restoration because price facts cannot be deleted."""
        raise RuntimeError("Prices are immutable.")

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.rental.tariff_enums import RentalTariffStatus, RentalTariffType
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable enum values for database persistence."""
    return [member.value for member in enum_class]


def _money_text(value: Decimal) -> str:
    """Format RUB without losing kopecks or introducing binary floats."""
    normalized = value.quantize(Decimal("0.01"))
    whole, fraction = f"{normalized:.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", " ")
    return f"{grouped} ₽" if fraction == "00" else f"{grouped},{fraction} ₽"


class RentalTariffRecord(BaseModel):
    """One reproducible version of commercial rental terms for a CatalogVariant."""

    __tablename__ = "rental_tariffs"
    __table_args__ = (
        UniqueConstraint("lineage_id", "version", name="uq_rental_tariffs_lineage_version"),
        CheckConstraint("price >= 0", name="ck_rental_tariffs_price_nonnegative"),
        CheckConstraint("grace_minutes >= 0", name="ck_rental_tariffs_grace_nonnegative"),
        CheckConstraint(
            "deposit_amount IS NULL OR deposit_amount >= 0",
            name="ck_rental_tariffs_deposit_nonnegative",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_rental_tariffs_valid_period",
        ),
        CheckConstraint(
            "(type = 'duration' AND duration_minutes > 0 "
            "AND slot_start IS NULL AND slot_end IS NULL) OR "
            "(type = 'slot' AND duration_minutes IS NULL "
            "AND slot_start IS NOT NULL AND slot_end IS NOT NULL "
            "AND slot_end > slot_start)",
            name="ck_rental_tariffs_type_fields",
        ),
        Index(
            "ix_rental_tariffs_variant_selectable",
            "variant_id",
            "status",
            "valid_from",
            "valid_to",
        ),
    )

    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    lineage_id: Mapped[UUIDv7] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[RentalTariffType] = mapped_column(
        Enum(RentalTariffType, name="rental_tariff_type", values_callable=_enum_values),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slot_start: Mapped[time | None] = mapped_column(Time(), nullable=True)
    slot_end: Mapped[time | None] = mapped_column(Time(), nullable=True)
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    requires_agreement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[RentalTariffStatus] = mapped_column(
        Enum(RentalTariffStatus, name="rental_tariff_status", values_callable=_enum_values),
        nullable=False,
        default=RentalTariffStatus.ACTIVE,
        server_default=RentalTariffStatus.ACTIVE.value,
        index=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_snapshot(self) -> dict[str, str | int | bool | None]:
        """Return deterministic, JSON-safe terms for a future Rental snapshot."""
        return {
            "tariff_id": str(self.id),
            "tariff_version": self.version,
            "name": self.name,
            "type": self.type.value,
            "price": f"{self.price.quantize(Decimal('0.01')):.2f}",
            "duration_minutes": self.duration_minutes,
            "slot_start": self.slot_start.strftime("%H:%M") if self.slot_start else None,
            "slot_end": self.slot_end.strftime("%H:%M") if self.slot_end else None,
            "grace_minutes": self.grace_minutes,
            "deposit_amount": (
                f"{self.deposit_amount.quantize(Decimal('0.01')):.2f}"
                if self.deposit_amount is not None
                else None
            ),
            "requires_agreement": self.requires_agreement,
        }

    def format_terms(self) -> str:
        """Render reusable human-readable terms from this exact tariff version."""
        period = (
            self.name
            if self.type is RentalTariffType.DURATION
            else f"{self.slot_start:%H:%M}–{self.slot_end:%H:%M}"
        )
        lines = [f"{period} · {_money_text(self.price)}"]
        lines.append(f"Льготный период: {self.grace_minutes} мин")
        if self.deposit_amount is not None:
            lines.append(f"Обеспечительный платёж: {_money_text(self.deposit_amount)}")
        return "\n".join(lines)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Archive instead of hiding a commercial version through generic soft deletion."""
        self.status = RentalTariffStatus.ARCHIVED
        self.updated_by_id = actor_id

    def restore(self) -> None:
        """Require an explicit new version rather than restoring old commercial terms."""
        raise RuntimeError("Archived rental tariff versions cannot be restored.")

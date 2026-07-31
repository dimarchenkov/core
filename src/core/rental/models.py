from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.order_enums import RentalOrderItemStatus, RentalOrderStatus
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for database enum persistence."""
    return [member.value for member in enum_class]


class RentalAssetRecord(BaseModel):
    """Persisted state projection for one RentalAsset aggregate."""

    __tablename__ = "rental_assets"

    asset_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    intake_item_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("intake_item_drafts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[AssetPurpose] = mapped_column(
        Enum(AssetPurpose, name="asset_purpose", values_callable=_enum_values),
        nullable=False,
    )
    condition: Mapped[AssetCondition] = mapped_column(
        Enum(AssetCondition, name="asset_condition", values_callable=_enum_values),
        nullable=False,
    )
    availability: Mapped[RentalAvailability] = mapped_column(
        Enum(
            RentalAvailability,
            name="rental_availability",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    retirement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because RentalAsset identity and history are permanent."""
        del actor_id
        raise RuntimeError("Rental assets cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because RentalAsset records cannot be deleted."""
        raise RuntimeError("Rental assets cannot be restored.")


class RentalOrderRecord(BaseModel):
    """Persisted current-state projection of one RentalOrder aggregate."""

    __tablename__ = "rental_orders"

    order_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )
    customer_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_phone_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[RentalOrderStatus] = mapped_column(
        Enum(RentalOrderStatus, name="rental_order_status", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    planned_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_return_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deposit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[RentalOrderItemRecord]] = relationship(
        "RentalOrderItemRecord",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="RentalOrderItemRecord.created_at",
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because rental order history is permanent."""
        del actor_id
        raise RuntimeError("Rental orders cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because rental orders cannot be deleted."""
        raise RuntimeError("Rental orders cannot be restored.")


class RentalOrderItemRecord(BaseModel):
    """Persisted entity projection owned by one RentalOrder."""

    __tablename__ = "rental_order_items"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "rental_asset_id",
            name="uq_rental_order_items_order_asset",
        ),
    )

    order_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_orders.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rental_asset_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_number_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    agreed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    charged_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    return_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RentalOrderItemStatus] = mapped_column(
        Enum(
            RentalOrderItemStatus,
            name="rental_order_item_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )

    order: Mapped[RentalOrderRecord] = relationship(
        "RentalOrderRecord",
        back_populates="items",
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion after persistence because order item history is permanent."""
        del actor_id
        raise RuntimeError("Persisted rental order items cannot be deleted directly.")

    def restore(self) -> None:
        """Reject restoration because order items cannot be soft-deleted."""
        raise RuntimeError("Rental order items cannot be restored.")

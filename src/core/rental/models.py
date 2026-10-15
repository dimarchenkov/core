from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability, RentalSealStatus
from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalOverdueState,
    RentalReturnCondition,
    RentalTimingSource,
)
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for database enum persistence."""
    return [member.value for member in enum_class]


class RentalAssetRecord(BaseModel):
    """Persisted state projection for one RentalAsset aggregate."""

    __tablename__ = "rental_assets"

    asset_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    intake_item_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("intake_item_drafts.id", ondelete="RESTRICT"),
        nullable=True,
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
    acquisition_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    seals: Mapped[list[RentalAssetSealRecord]] = relationship(
        "RentalAssetSealRecord",
        back_populates="rental_asset",
        order_by="RentalAssetSealRecord.installed_at",
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because RentalAsset identity and history are permanent."""
        del actor_id
        raise RuntimeError("Rental assets cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because RentalAsset records cannot be deleted."""
        raise RuntimeError("Rental assets cannot be restored.")


class RentalAssetSealRecord(BaseModel):
    """One append-only seal identity attached to a physical rental asset."""

    __tablename__ = "rental_asset_seals"

    rental_asset_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    location: Mapped[str] = mapped_column(String(160), nullable=False)
    seal_number: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[RentalSealStatus] = mapped_column(
        Enum(RentalSealStatus, name="rental_seal_status", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rental_asset: Mapped[RentalAssetRecord] = relationship(
        "RentalAssetRecord", back_populates="seals"
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because seal identity and replacement history are permanent."""
        del actor_id
        raise RuntimeError("Rental asset seals cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because seal records cannot be deleted."""
        raise RuntimeError("Rental asset seals cannot be restored.")


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
    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
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
        CheckConstraint(
            "overdue_minutes IS NULL OR overdue_minutes >= 0",
            name="ck_rental_order_items_overdue_minutes_nonnegative",
        ),
        CheckConstraint(
            "overdue_charge IS NULL OR overdue_charge >= 0",
            name="ck_rental_order_items_overdue_charge_nonnegative",
        ),
        CheckConstraint(
            "damage_charge IS NULL OR damage_charge >= 0",
            name="ck_rental_order_items_damage_charge_nonnegative",
        ),
        CheckConstraint(
            "deposit_applied IS NULL OR deposit_applied >= 0",
            name="ck_rental_order_items_deposit_applied_nonnegative",
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
    rental_tariff_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("rental_tariffs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    rental_tariff_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timing_source: Mapped[RentalTimingSource | None] = mapped_column(
        Enum(RentalTimingSource, name="rental_timing_source", values_callable=_enum_values),
        nullable=True,
    )
    tariff_snapshot: Mapped[dict[str, str | int | bool | None] | None] = mapped_column(
        JSON, nullable=True
    )
    asset_identification_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    planned_rental_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    planned_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recommended_deposit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    charged_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    return_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_condition: Mapped[RentalReturnCondition | None] = mapped_column(
        Enum(
            RentalReturnCondition,
            name="rental_return_condition",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    overdue_state: Mapped[RentalOverdueState | None] = mapped_column(
        Enum(
            RentalOverdueState,
            name="rental_overdue_state",
            values_callable=_enum_values,
        ),
        nullable=True,
    )
    overdue_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overdue_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    damage_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    deposit_applied: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    return_calculation_snapshot: Mapped[dict[str, str | int | bool | None] | None] = mapped_column(
        JSON, nullable=True
    )
    seal_verification_snapshot: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
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


class RentalMaintenanceRecord(BaseModel):
    """Append-only service journal entry for one physical rental asset."""

    __tablename__ = "rental_maintenance_records"

    rental_asset_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    performer_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    result: Mapped[str] = mapped_column(Text, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because maintenance history is permanent."""
        del actor_id
        raise RuntimeError("Rental maintenance records cannot be deleted.")


class RentalDamageRecord(BaseModel):
    """Append-only observed damage linked to a physical asset and optional rental item."""

    __tablename__ = "rental_damage_records"

    rental_asset_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_item_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("rental_order_items.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    recorded_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because damage observations are permanent."""
        del actor_id
        raise RuntimeError("Rental damage records cannot be deleted.")


class RentalConditionPhotoRecord(BaseModel):
    """Link a Media image to a before/after observation of one rental asset."""

    __tablename__ = "rental_condition_photos"

    rental_asset_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    order_item_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("rental_order_items.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    image_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("images.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    recorded_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because condition evidence is permanent."""
        del actor_id
        raise RuntimeError("Rental condition photos cannot be deleted.")

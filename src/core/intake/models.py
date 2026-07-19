from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.intake.enums import IntakeItemKind, IntakeSessionStatus
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable values for database enum persistence."""
    return [member.value for member in enum_class]


class IntakeSession(BaseModel):
    """Employee-owned resumable workspace that precedes a warehouse Receipt."""

    __tablename__ = "intake_sessions"

    owner_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[IntakeSessionStatus] = mapped_column(
        Enum(
            IntakeSessionStatus,
            name="intake_session_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=IntakeSessionStatus.DRAFT,
        server_default=IntakeSessionStatus.DRAFT.value,
        index=True,
    )
    supplier_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    receipt_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("receipts.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandonment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[IntakeItemDraft]] = relationship(
        "IntakeItemDraft",
        back_populates="session",
        order_by="IntakeItemDraft.created_at",
    )


class IntakeItemDraft(BaseModel):
    """Persisted incomplete position inside an IntakeSession."""

    __tablename__ = "intake_item_drafts"
    __table_args__ = (
        CheckConstraint(
            "quantity IS NULL OR quantity > 0",
            name="ck_intake_item_drafts_quantity_positive",
        ),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_intake_item_drafts_purchase_price_nonnegative",
        ),
        CheckConstraint(
            "kind != 'existing_variant' OR variant_id IS NOT NULL",
            name="ck_intake_item_drafts_existing_variant_required",
        ),
        CheckConstraint(
            "kind = 'existing_variant' OR image_id IS NOT NULL",
            name="ck_intake_item_drafts_new_image_required",
        ),
        CheckConstraint(
            "kind != 'new_variant' OR product_id IS NOT NULL",
            name="ck_intake_item_drafts_new_variant_product_required",
        ),
    )

    session_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("intake_sessions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[IntakeItemKind] = mapped_column(
        Enum(IntakeItemKind, name="intake_item_kind", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("catalog_products.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    image_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("images.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    variant_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributes: Mapped[dict[str, str | int | bool]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandonment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[IntakeSession] = relationship("IntakeSession", back_populates="items")

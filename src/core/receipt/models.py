from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.receipt.enums import ReceiptStatus
from core.shared.db import BaseModel, UUIDv7


def _receipt_status_values(enum_class: type[ReceiptStatus]) -> list[str]:
    """Return database values for the receipt lifecycle enum."""
    return [status.value for status in enum_class]


class Receipt(BaseModel):
    """Draft supplier delivery record that does not affect stock by itself."""

    __tablename__ = "receipts"

    number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    supplier_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus, name="receipt_status", values_callable=_receipt_status_values),
        nullable=False,
        default=ReceiptStatus.DRAFT,
        server_default=ReceiptStatus.DRAFT.value,
    )
    source_document_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[ReceiptItem]] = relationship("ReceiptItem", back_populates="receipt")


class ReceiptItem(BaseModel):
    """One draft delivery line for an existing catalog variant and unit purchase price."""

    __tablename__ = "receipt_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_receipt_items_quantity_positive"),
        CheckConstraint("purchase_price >= 0", name="ck_receipt_items_purchase_price_nonnegative"),
    )

    receipt_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("receipts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    receipt: Mapped[Receipt] = relationship("Receipt", back_populates="items")

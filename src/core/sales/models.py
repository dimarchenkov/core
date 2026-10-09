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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.integrations.enums import IntegrationProvider
from core.sales.enums import (
    FiscalizationStatus,
    PaymentMethod,
    PaymentStatus,
    SaleDiscountType,
    SaleItemSource,
    SaleStatus,
)
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for database-backed Sales enums."""
    return [member.value for member in enum_class]


class Sale(BaseModel):
    """One persistent operator-owned shopping cart in the Sales workflow."""

    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("subtotal_amount >= 0", name="ck_sales_subtotal_nonnegative"),
        CheckConstraint(
            "discount_value >= 0 AND discount_value <= 99.99",
            name="ck_sales_discount_value_range",
        ),
        CheckConstraint("discount_amount >= 0", name="ck_sales_discount_amount_nonnegative"),
        CheckConstraint("total_amount >= 0", name="ck_sales_total_amount_nonnegative"),
        CheckConstraint(
            "total_amount = subtotal_amount - discount_amount",
            name="ck_sales_total_arithmetic",
        ),
    )

    sale_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    owner_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[SaleStatus] = mapped_column(
        Enum(SaleStatus, name="sale_status", values_callable=_enum_values),
        nullable=False,
        default=SaleStatus.DRAFT,
        server_default=SaleStatus.DRAFT.value,
        index=True,
    )
    subtotal_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    discount_type: Mapped[SaleDiscountType] = mapped_column(
        Enum(SaleDiscountType, name="sale_discount_type", values_callable=_enum_values),
        nullable=False,
        default=SaleDiscountType.PERCENT,
        server_default=SaleDiscountType.PERCENT.value,
    )
    discount_value: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default="0.00"
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="RUB",
        server_default="RUB",
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    inventory_posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[SaleItem]] = relationship(
        "SaleItem",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SaleItem.created_at, SaleItem.id",
    )
    payments: Mapped[list[PaymentAttempt]] = relationship(
        "PaymentAttempt",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PaymentAttempt.attempt_number",
    )
    fiscalization: Mapped[Fiscalization | None] = relationship(
        "Fiscalization",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )


class SaleItem(BaseModel):
    """Commercial snapshot of one Variant added to a Sale."""

    __tablename__ = "sale_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_sale_items_unit_price_nonnegative"),
        CheckConstraint("line_total >= 0", name="ck_sale_items_line_total_nonnegative"),
        CheckConstraint(
            "fiscal_line_total >= 0", name="ck_sale_items_fiscal_line_total_nonnegative"
        ),
        CheckConstraint(
            "(source = 'catalog' AND variant_id IS NOT NULL) OR "
            "(source = 'manual' AND variant_id IS NULL)",
            name="ck_sale_items_source_variant",
        ),
        UniqueConstraint("sale_id", "variant_id", name="uq_sale_items_sale_variant"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[SaleItemSource] = mapped_column(
        Enum(SaleItemSource, name="sale_item_source", values_callable=_enum_values),
        nullable=False,
        default=SaleItemSource.CATALOG,
        server_default=SaleItemSource.CATALOG.value,
        index=True,
    )
    variant_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    product_title_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variant_title_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_label_snapshot: Mapped[str] = mapped_column(String(511), nullable=False)
    sku_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    barcode_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fiscal_line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fiscal_allocations: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="RUB",
        server_default="RUB",
    )

    sale: Mapped[Sale] = relationship("Sale", back_populates="items")


class PaymentAttempt(BaseModel):
    """One durable acquiring attempt; failed attempts are never reused as new charges."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("requested_amount > 0", name="ck_payment_attempts_amount_positive"),
        CheckConstraint(
            "(payment_method = 'cash' AND integration_id IS NULL AND provider IS NULL) OR "
            "(payment_method = 'card' AND integration_id IS NOT NULL AND provider IS NOT NULL)",
            name="ck_payment_attempts_method_provider",
        ),
        CheckConstraint(
            "payment_method != 'card' OR fiscalization_required",
            name="ck_payment_attempts_card_requires_fiscalization",
        ),
        UniqueConstraint("sale_id", "attempt_number", name="uq_payment_attempts_sale_number"),
        UniqueConstraint("idempotency_key", name="uq_payment_attempts_idempotency_key"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    provider: Mapped[IntegrationProvider | None] = mapped_column(
        Enum(IntegrationProvider, name="integration_provider", values_callable=_enum_values),
        nullable=True,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method", values_callable=_enum_values),
        nullable=False,
    )
    requested_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fiscalization_required: Mapped[bool] = mapped_column(
        nullable=False,
        default=True,
        server_default="true",
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="RUB")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", values_callable=_enum_values),
        nullable=False,
        default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    sale: Mapped[Sale] = relationship("Sale", back_populates="payments")


class Fiscalization(BaseModel):
    """Single retryable fiscal obligation for one successfully paid Sale."""

    __tablename__ = "fiscalizations"
    __table_args__ = (
        CheckConstraint("fiscal_amount > 0", name="ck_fiscalizations_amount_positive"),
        CheckConstraint(
            "(status = 'skipped' AND integration_id IS NULL AND provider IS NULL) OR "
            "(status != 'skipped' AND integration_id IS NOT NULL AND provider IS NOT NULL)",
            name="ck_fiscalizations_status_provider",
        ),
        UniqueConstraint("sale_id", name="uq_fiscalizations_sale_id"),
        UniqueConstraint("idempotency_key", name="uq_fiscalizations_idempotency_key"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_attempt_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    integration_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("integrations.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    provider: Mapped[IntegrationProvider | None] = mapped_column(
        Enum(IntegrationProvider, name="integration_provider", values_callable=_enum_values),
        nullable=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_receipt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[FiscalizationStatus] = mapped_column(
        Enum(FiscalizationStatus, name="fiscalization_status", values_callable=_enum_values),
        nullable=False,
        default=FiscalizationStatus.PENDING,
        server_default=FiscalizationStatus.PENDING.value,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    fiscal_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="RUB")
    provider_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    sale: Mapped[Sale] = relationship("Sale", back_populates="fiscalization")

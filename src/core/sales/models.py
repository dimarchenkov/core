from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.integrations.enums import IntegrationProvider
from core.sales.enums import (
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationJobStatus,
    FiscalizationStatus,
    IncidentKind,
    IncidentResolutionStatus,
    IncidentResolutionType,
    PaymentJobStatus,
    PaymentMethod,
    PaymentStatus,
    SaleDiscountType,
    SaleItemSource,
    SaleStatus,
)
from core.shared.db import Base, BaseModel, UUIDPrimaryKeyMixin, UUIDv7, generate_uuid_v7


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
    payment_jobs: Mapped[list[PaymentJob]] = relationship(
        "PaymentJob",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PaymentJob.created_at",
    )
    fiscalization: Mapped[Fiscalization | None] = relationship(
        "Fiscalization",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )
    fiscalization_job: Mapped[FiscalizationJob | None] = relationship(
        "FiscalizationJob",
        back_populates="sale",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )
    incident_resolutions: Mapped[list[FinancialIncidentResolution]] = relationship(
        "FinancialIncidentResolution",
        back_populates="sale",
        lazy="selectin",
        order_by="FinancialIncidentResolution.created_at",
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

    @property
    def accounting_superseded(self) -> bool:
        """Exclude audited bookkeeping corrections without rewriting provider history."""
        return bool((self.provider_metadata or {}).get("accounting_superseded", False))

    @property
    def emergency_fallback(self) -> bool:
        """Identify an operator-confirmed payment completed on the physical POS."""
        return (self.provider_metadata or {}).get("source") in {
            "EXTERNAL_POS",
            "external_pos",
        }

    @property
    def duplicate_payment_risk(self) -> bool:
        """Expose a retained late provider approval that requires investigation."""
        return bool((self.provider_metadata or {}).get("late_provider_approval_detected", False))

    @property
    def linked_payment_attempt_id(self) -> str | None:
        """Return the unresolved acquiring attempt linked to an emergency fallback."""
        value = (self.provider_metadata or {}).get("linked_payment_attempt_id")
        return str(value) if value else None

    @property
    def cancellation_requested(self) -> bool:
        """Expose a durable operator cancellation intent without provider overclaiming."""
        intent = (self.provider_metadata or {}).get("cancellation_intent")
        return isinstance(intent, dict) and intent.get("state") == "requested"

    @property
    def selected_fiscal_integration_id(self) -> UUIDv7 | None:
        """Return the durable fiscal selection, falling back for historical rows."""
        value = (self.provider_metadata or {}).get("fiscal_integration_id")
        return UUID(str(value)) if value else self.integration_id

    job: Mapped[PaymentJob | None] = relationship(
        "PaymentJob",
        back_populates="payment_attempt",
        cascade="all, delete-orphan",
        lazy="selectin",
        uselist=False,
    )


class PaymentJob(BaseModel):
    """Durable worker-owned continuation for one acquiring attempt."""

    __tablename__ = "payment_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_payment_jobs_attempt_nonnegative"),
        UniqueConstraint("payment_attempt_id", name="uq_payment_jobs_attempt_id"),
        UniqueConstraint("correlation_id", name="uq_payment_jobs_correlation_id"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_attempt_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[PaymentJobStatus] = mapped_column(
        Enum(PaymentJobStatus, name="payment_job_status", values_callable=_enum_values),
        nullable=False,
        default=PaymentJobStatus.QUEUED,
        server_default=PaymentJobStatus.QUEUED.value,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=lambda: f"payment:{generate_uuid_v7()}",
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    sale: Mapped[Sale] = relationship("Sale", back_populates="payment_jobs")
    payment_attempt: Mapped[PaymentAttempt] = relationship("PaymentAttempt", back_populates="job")


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

    @property
    def operator_dismissed(self) -> bool:
        """Record operational dismissal without claiming a receipt exists or voiding payment."""
        return bool((self.provider_metadata or {}).get("operator_dismissed", False))

    @property
    def external_receipt_confirmed(self) -> bool:
        """Distinguish a physical-POS receipt from a sale intentionally made without one."""
        return bool((self.provider_metadata or {}).get("external_receipt_confirmed", False))


class FiscalizationJob(BaseModel):
    """Durable background work item that owns fiscalization continuation."""

    __tablename__ = "fiscalization_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_fiscalization_jobs_attempt_nonnegative"),
        UniqueConstraint("sale_id", name="uq_fiscalization_jobs_sale_id"),
        UniqueConstraint("fiscalization_id", name="uq_fiscalization_jobs_fiscalization_id"),
        UniqueConstraint("correlation_id", name="uq_fiscalization_jobs_correlation_id"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fiscalization_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("fiscalizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[FiscalizationJobStatus] = mapped_column(
        Enum(
            FiscalizationJobStatus,
            name="fiscalization_job_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=FiscalizationJobStatus.QUEUED,
        server_default=FiscalizationJobStatus.QUEUED.value,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default=lambda: f"fiscal:{generate_uuid_v7()}",
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aqsi_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    sale: Mapped[Sale] = relationship("Sale", back_populates="fiscalization_job")

    @property
    def provider_operation_id(self) -> str | None:
        """Expose the legacy AQSI column through a provider-neutral worker name."""
        return self.aqsi_operation_id

    @provider_operation_id.setter
    def provider_operation_id(self, value: str | None) -> None:
        """Persist a generic operation ID in the backward-compatible legacy column."""
        self.aqsi_operation_id = value


class FinancialOperationEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only evidence for meaningful payment and fiscal transitions."""

    __tablename__ = "financial_operation_events"
    __table_args__ = (
        Index("ix_financial_events_entity", "entity_type", "entity_id"),
        Index("ix_financial_events_sale_occurred", "sale_id", "occurred_at"),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    entity_type: Mapped[FinancialEntityType] = mapped_column(
        Enum(
            FinancialEntityType,
            name="financial_entity_type",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    entity_id: Mapped[UUIDv7] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[FinancialEventType] = mapped_column(
        Enum(
            FinancialEventType,
            name="financial_event_type",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    previous_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[IntegrationProvider | None] = mapped_column(
        Enum(IntegrationProvider, name="integration_provider", values_callable=_enum_values),
        nullable=True,
    )
    provider_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[FinancialActorType] = mapped_column(
        Enum(
            FinancialActorType,
            name="financial_actor_type",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    actor_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        index=True,
    )

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject removal because financial events are immutable evidence."""
        del actor_id
        raise RuntimeError("Financial operation events are immutable.")

    def restore(self) -> None:
        """Reject restoration because financial events cannot be deleted."""
        raise RuntimeError("Financial operation events are immutable.")


@event.listens_for(FinancialOperationEvent, "before_update")
@event.listens_for(FinancialOperationEvent, "before_delete")
def _prevent_financial_event_mutation(*_: object) -> None:
    """Reject ORM mutation even when callers bypass normal application APIs."""
    raise RuntimeError("Financial operation events are immutable.")


class FinancialIncidentResolution(BaseModel):
    """Current durable conclusion for one independently reviewable incident track."""

    __tablename__ = "financial_incident_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "incident_kind",
            "incident_id",
            name="uq_financial_incident_resolution_kind_id",
        ),
        Index(
            "ix_financial_incident_resolutions_sale_kind",
            "sale_id",
            "incident_kind",
        ),
    )

    sale_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    incident_id: Mapped[UUIDv7] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    incident_kind: Mapped[IncidentKind] = mapped_column(
        Enum(IncidentKind, name="incident_kind", values_callable=_enum_values),
        nullable=False,
    )
    resolution_type: Mapped[IncidentResolutionType] = mapped_column(
        Enum(
            IncidentResolutionType,
            name="incident_resolution_type",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    resolution_status: Mapped[IncidentResolutionStatus] = mapped_column(
        Enum(
            IncidentResolutionStatus,
            name="incident_resolution_status",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    resolved_by_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    evidence_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fiscal_fn: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fiscal_fd: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fiscal_fpd: Mapped[str | None] = mapped_column(String(128), nullable=True)

    sale: Mapped[Sale] = relationship("Sale", back_populates="incident_resolutions")

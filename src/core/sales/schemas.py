from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, computed_field, model_validator

from core.integrations.enums import IntegrationProvider
from core.sales.enums import (
    CheckoutPaymentOption,
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationJobStatus,
    FiscalizationStatus,
    FiscalVerificationOutcome,
    IncidentKind,
    IncidentResolutionChoice,
    IncidentResolutionStatus,
    IncidentResolutionType,
    PaymentJobStatus,
    PaymentMethod,
    PaymentStatus,
    SaleDiscountType,
    SaleItemSource,
    SaleStatus,
)
from core.shared.db import UUIDv7


class SaleItemAddVariant(PydanticBaseModel):
    """Command payload for adding a Catalog Variant to a Sale."""

    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7


class SaleItemAddBarcode(PydanticBaseModel):
    """Command payload for resolving and adding a canonical barcode."""

    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=128)


class SaleItemQuantityChange(PydanticBaseModel):
    """Relative quantity command that avoids stale absolute client values."""

    model_config = ConfigDict(extra="forbid")

    delta: int = Field(ge=-1, le=1)


class SaleItemAddManual(PydanticBaseModel):
    """Command payload for an uncatalogued one-off SaleItem."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=511)
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(default=1, ge=1, le=9999)


class SaleDiscountUpdate(PydanticBaseModel):
    """Command payload for the supported receipt-level percentage discount."""

    model_config = ConfigDict(extra="forbid")

    discount_value: Decimal = Field(
        ge=Decimal("0"), le=Decimal("99.99"), max_digits=4, decimal_places=2
    )


class SaleItemRead(PydanticBaseModel):
    """Immutable commercial snapshot plus current cart quantity."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    source: SaleItemSource
    variant_id: UUIDv7 | None
    product_title_snapshot: str | None
    variant_title_snapshot: str | None
    display_label_snapshot: str
    sku_snapshot: str | None
    barcode_snapshot: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    fiscal_line_total: Decimal
    currency: str


class PaymentJobRead(PydanticBaseModel):
    """Safe acquiring worker state visible in operational history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    status: PaymentJobStatus
    attempt_count: int
    next_retry_at: datetime | None
    last_attempt_at: datetime | None
    completed_at: datetime | None
    correlation_id: str
    dispatch_started_at: datetime | None
    reconciliation_requested_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class PaymentAttemptRead(PydanticBaseModel):
    """Safe acquiring history without card data or raw provider payloads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    integration_id: UUIDv7 | None
    provider: IntegrationProvider | None
    payment_method: PaymentMethod
    requested_amount: Decimal
    fiscalization_required: bool
    currency: str
    external_id: str | None
    status: PaymentStatus
    accounting_superseded: bool = False
    emergency_fallback: bool = False
    duplicate_payment_risk: bool = False
    cancellation_requested: bool = False
    linked_payment_attempt_id: str | None = None
    attempt_number: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    job: PaymentJobRead | None


class FiscalizationRead(PydanticBaseModel):
    """Safe fiscal obligation state separate from acquiring."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    integration_id: UUIDv7 | None
    provider: IntegrationProvider | None
    external_id: str | None
    external_receipt_id: str | None
    status: FiscalizationStatus
    operator_dismissed: bool = False
    external_receipt_confirmed: bool = False
    attempt_count: int
    fiscal_amount: Decimal
    currency: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class FiscalizationJobRead(PydanticBaseModel):
    """Safe background execution state visible to operators."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    status: FiscalizationJobStatus
    attempt_count: int
    next_retry_at: datetime | None
    last_attempt_at: datetime | None
    completed_at: datetime | None
    correlation_id: str
    dispatch_started_at: datetime | None
    reconciliation_requested_at: datetime | None
    provider_operation_id: str | None
    aqsi_operation_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class IncidentResolutionRead(PydanticBaseModel):
    """Safe operator resolution state linked to immutable financial evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    incident_id: UUIDv7
    incident_kind: IncidentKind
    resolution_type: IncidentResolutionType
    resolution_status: IncidentResolutionStatus
    resolved_by_id: UUIDv7
    resolved_at: datetime
    evidence_reference: str | None
    operator_note: str | None
    fiscal_fn: str | None
    fiscal_fd: str | None
    fiscal_fpd: str | None


class SaleRead(PydanticBaseModel):
    """Complete server-authoritative Sale cart returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    sale_number: int
    owner_id: UUIDv7
    status: SaleStatus
    subtotal_amount: Decimal
    discount_type: SaleDiscountType
    discount_value: Decimal
    discount_amount: Decimal
    total_amount: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None
    paid_at: datetime | None
    inventory_posted_at: datetime | None
    completed_at: datetime | None
    items: list[SaleItemRead]
    payments: list[PaymentAttemptRead]
    fiscalization: FiscalizationRead | None
    fiscalization_job: FiscalizationJobRead | None
    incident_resolutions: list[IncidentResolutionRead] = Field(default_factory=list)

    @computed_field
    @property
    def item_quantity(self) -> int:
        """Return the total number of units currently in the cart."""
        return sum(item.quantity for item in self.items)

    @computed_field
    @property
    def line_count(self) -> int:
        """Return the number of distinct Variant lines in the cart."""
        return len(self.items)


class CheckoutStockWarningRead(PydanticBaseModel):
    """Non-blocking negative-stock projection shown before checkout."""

    variant_id: UUIDv7
    label: str
    on_hand: Decimal
    after_sale: Decimal


class CheckoutContextRead(PydanticBaseModel):
    """Operator confirmation facts without secret provider configuration."""

    available: bool
    message: str
    integration_id: UUIDv7 | None = None
    integration_name: str | None = None
    provider_name: str | None = None
    payment_integration_id: UUIDv7 | None = None
    payment_integration_name: str | None = None
    payment_provider_name: str | None = None
    fiscal_integration_id: UUIDv7 | None = None
    fiscal_integration_name: str | None = None
    fiscal_provider_name: str | None = None
    acquiring_label: str | None = None
    fiscalization_available: bool = False
    stock_warnings: list[CheckoutStockWarningRead] = Field(default_factory=list)


class CheckoutStartRequest(PydanticBaseModel):
    """Operator-confirmed tender choice for a new payment fact."""

    model_config = ConfigDict(extra="forbid")

    payment_option: CheckoutPaymentOption = CheckoutPaymentOption.CARD


class EmergencyFallbackRequest(PydanticBaseModel):
    """Explicit acknowledgement for a sensitive external-POS accounting override."""

    model_config = ConfigDict(extra="forbid")

    risk_acknowledged: bool
    operator_note: str | None = Field(default=None, min_length=3, max_length=1000)


class SingleChargeResolutionRequest(PydanticBaseModel):
    """Explicit operator attestation after comparing AQSI transaction records."""

    model_config = ConfigDict(extra="forbid")

    confirmation: bool
    evidence_reference: str = Field(min_length=3, max_length=255)
    operator_note: str | None = Field(default=None, min_length=3, max_length=1000)


class FiscalVerificationRequest(PydanticBaseModel):
    """Non-mutating fiscal evidence review; it never asks a provider to issue a receipt."""

    model_config = ConfigDict(extra="forbid")

    outcome: FiscalVerificationOutcome
    confirmation: bool
    evidence_reference: str | None = Field(default=None, min_length=3, max_length=255)
    operator_note: str | None = Field(default=None, min_length=3, max_length=1000)
    fiscal_fn: str | None = Field(default=None, min_length=1, max_length=64)
    fiscal_fd: str | None = Field(default=None, min_length=1, max_length=64)
    fiscal_fpd: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_evidence(self) -> FiscalVerificationRequest:
        """Require evidence for a verified receipt and a note for unresolved outcomes."""
        if self.outcome is FiscalVerificationOutcome.VERIFIED and not any(
            (self.evidence_reference, self.fiscal_fn, self.fiscal_fd, self.fiscal_fpd)
        ):
            raise ValueError("Для подтверждения чека укажите ссылку или фискальные реквизиты")
        if self.outcome is not FiscalVerificationOutcome.VERIFIED and not self.operator_note:
            raise ValueError("Для результата проверки добавьте комментарий оператора")
        return self


class EmergencyIncidentResolutionRequest(PydanticBaseModel):
    """One explicit operator choice; it never rewrites provider facts."""

    model_config = ConfigDict(extra="forbid")

    choice: IncidentResolutionChoice
    confirmation: bool
    operator_note: str | None = Field(default=None, min_length=3, max_length=1000)


class EmergencyIncidentResolutionRead(PydanticBaseModel):
    """Result of recording one compact incident action."""

    choice: IncidentResolutionChoice
    message: str


class CheckoutHealthRead(PydanticBaseModel):
    """Multi-signal provider state without overstating physical readiness."""

    status: str
    api_reachable: bool | None
    payment_provider_reachable: bool | None = None
    fiscal_provider_reachable: bool | None = None
    physical_device_ready: bool | None = None
    configured: bool
    payment_provider_configured: bool = False
    fiscal_provider_configured: bool = False
    active_operation: bool
    local_active_operation: bool = False
    global_active_operation: bool = False
    manual_review_required: bool = False
    worker_running: bool = False
    worker_last_heartbeat: datetime | None = None
    queued_job_count: int = 0
    processing_job_count: int = 0
    oldest_queued_job_age_seconds: int | None = None
    stalled_job_count: int = 0
    last_worker_error: str | None = None
    message: str
    checked_at: datetime
    integration_name: str | None = None
    payment_integration_name: str | None = None
    fiscal_integration_name: str | None = None
    failed_fiscalization_count: int = 0


class FinancialOperationEventRead(PydanticBaseModel):
    """Sanitized immutable event exposed only through an owned Sale detail."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    entity_type: FinancialEntityType
    entity_id: UUIDv7
    event_type: FinancialEventType
    previous_status: str | None
    new_status: str | None
    provider: IntegrationProvider | None
    provider_operation_id: str | None
    actor_type: FinancialActorType
    actor_id: UUIDv7 | None
    correlation_id: str
    error_code: str | None
    safe_message: str | None
    metadata_json: dict[str, object]
    occurred_at: datetime


class CheckoutOperationDetailRead(PydanticBaseModel):
    """Owned Sale plus bounded recovery diagnostics and immutable timeline."""

    sale: SaleRead
    events: list[FinancialOperationEventRead]
    recovery_state: str
    manual_review_required: bool
    manual_review_payment_id: UUIDv7 | None = None
    emergency_fallback_recorded: bool = False
    emergency_fallback_eligible: bool = False
    duplicate_payment_risk: bool = False
    payment_incident_unresolved: bool = False
    can_confirm_single_charge: bool = False
    payment_resolution: IncidentResolutionRead | None = None
    fiscal_verification_state: str = "not_required"
    fiscal_action_required: bool = False
    fiscal_resolution: IncidentResolutionRead | None = None
    operator_instruction: str | None = None
    provider_identity: str | None = None
    correlation_ids: list[str] = Field(default_factory=list)
    provider_operation_ids: list[str] = Field(default_factory=list)

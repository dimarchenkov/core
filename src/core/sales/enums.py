from __future__ import annotations

from enum import StrEnum


class SaleStatus(StrEnum):
    """Commercial lifecycle kept separate from payment/fiscal attempt facts."""

    DRAFT = "draft"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    FISCALIZATION_PENDING = "fiscalization_pending"
    COMPLETED = "completed"
    PAYMENT_FAILED = "payment_failed"
    FISCALIZATION_FAILED = "fiscalization_failed"
    CANCELLED = "cancelled"


class PaymentMethod(StrEnum):
    """Operator-selected tender types, independent from fiscalization."""

    CARD = "card"
    CASH = "cash"


class CheckoutPaymentOption(StrEnum):
    """Operator-visible checkout choices with explicit fiscal intent."""

    CARD = "card"
    CASH_WITH_RECEIPT = "cash_with_receipt"
    CASH_WITHOUT_RECEIPT = "cash_without_receipt"


class PaymentStatus(StrEnum):
    """Durable result of one provider acquiring attempt."""

    PENDING = "pending"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class SaleDiscountType(StrEnum):
    """Supported receipt-level discount mechanisms."""

    PERCENT = "percent"


class SaleItemSource(StrEnum):
    """Origin of a frozen commercial SaleItem snapshot."""

    CATALOG = "catalog"
    MANUAL = "manual"


class FiscalizationStatus(StrEnum):
    """Durable result of fiscalizing one paid Sale snapshot."""

    PENDING = "pending"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FiscalizationJobStatus(StrEnum):
    """Durable server-side execution state for one fiscal obligation."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentJobStatus(StrEnum):
    """Durable server-side execution state for one acquiring operation."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FinancialEntityType(StrEnum):
    """Domain records that can own one immutable financial event."""

    PAYMENT_ATTEMPT = "payment_attempt"
    FISCALIZATION = "fiscalization"
    PAYMENT_JOB = "payment_job"
    FISCALIZATION_JOB = "fiscalization_job"
    RECONCILIATION = "reconciliation"


class FinancialEventType(StrEnum):
    """Meaningful financial transitions retained for investigation and recovery."""

    PAYMENT_CREATED = "payment_created"
    PAYMENT_COMMAND_PREPARED = "payment_command_prepared"
    PAYMENT_DISPATCH_STARTED = "payment_dispatch_started"
    PAYMENT_OPERATION_ID_RECEIVED = "payment_operation_id_received"
    PAYMENT_APPROVED = "payment_approved"
    PAYMENT_DECLINED = "payment_declined"
    PAYMENT_CANCELED = "payment_canceled"
    PAYMENT_OUTCOME_UNKNOWN = "payment_outcome_unknown"
    FISCALIZATION_CREATED = "fiscalization_created"
    FISCAL_COMMAND_PREPARED = "fiscal_command_prepared"
    FISCAL_DISPATCH_STARTED = "fiscal_dispatch_started"
    FISCAL_OPERATION_ID_RECEIVED = "fiscal_operation_id_received"
    FISCALIZATION_SUCCEEDED = "fiscalization_succeeded"
    FISCALIZATION_FAILED = "fiscalization_failed"
    FISCAL_OUTCOME_UNKNOWN = "fiscal_outcome_unknown"
    RECONCILIATION_REQUESTED = "reconciliation_requested"
    RECONCILIATION_SUCCEEDED = "reconciliation_succeeded"
    RECONCILIATION_INCONCLUSIVE = "reconciliation_inconclusive"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    EMERGENCY_FALLBACK_REQUESTED = "emergency_fallback_requested"
    EMERGENCY_FALLBACK_CONFIRMED = "emergency_fallback_confirmed"
    EXTERNAL_PAYMENT_RECORDED = "external_payment_recorded"
    LATE_PROVIDER_APPROVAL_DETECTED = "late_provider_approval_detected"
    PAYMENT_INCIDENT_REVIEWED = "payment_incident_reviewed"
    SINGLE_CHARGE_OPERATOR_CONFIRMED = "single_charge_operator_confirmed"
    PAYMENT_INCIDENT_RESOLVED = "payment_incident_resolved"
    FISCAL_RECEIPT_MISSING_REPORTED = "fiscal_receipt_missing_reported"
    FISCAL_RECEIPT_VERIFIED = "fiscal_receipt_verified"
    FISCAL_VERIFICATION_UNKNOWN_RECORDED = "fiscal_verification_unknown_recorded"


class FinancialActorType(StrEnum):
    """Origin of a financial event without misattributing workers to employees."""

    OPERATOR = "operator"
    WORKER = "worker"
    SYSTEM = "system"


class IncidentKind(StrEnum):
    """Independent operator-review tracks for one financial incident."""

    PAYMENT_DUPLICATE_RISK = "payment_duplicate_risk"
    FISCAL_RECEIPT_VERIFICATION = "fiscal_receipt_verification"


class IncidentResolutionType(StrEnum):
    """Auditable operator conclusion without rewriting provider facts."""

    SINGLE_CHARGE_OPERATOR_CONFIRMED = "single_charge_operator_confirmed"
    EXTERNAL_POS_PAYMENT_CONFIRMED = "external_pos_payment_confirmed"
    ORIGINAL_OPERATION_CANCELED_CONFIRMED = "original_operation_canceled_confirmed"
    MANUAL_REVIEW_CONFIRMED = "manual_review_confirmed"
    FISCAL_RECEIPT_VERIFIED = "fiscal_receipt_verified"
    FISCAL_RECEIPT_MISSING = "fiscal_receipt_missing"
    FISCAL_VERIFICATION_UNKNOWN = "fiscal_verification_unknown"


class IncidentResolutionStatus(StrEnum):
    """Whether a review closes its track or still requires action."""

    RESOLVED = "resolved"
    REQUIRES_ACTION = "requires_action"
    UNKNOWN = "unknown"


class FiscalVerificationOutcome(StrEnum):
    """Operator-facing fiscal review outcomes kept separate from FiscalizationStatus."""

    VERIFIED = "verified"
    MISSING = "missing"
    UNKNOWN = "unknown"


class IncidentResolutionChoice(StrEnum):
    """Compact operator choices mapped to existing payment and fiscal review tracks."""

    CORRECTION_RECEIPT_FORMED = "correction_receipt_formed"
    EXTERNAL_POS_PAYMENT_CONFIRMED = "external_pos_payment_confirmed"
    ORIGINAL_OPERATION_CANCELED = "original_operation_canceled"
    MANUAL_REVIEWED = "manual_reviewed"

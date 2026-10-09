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

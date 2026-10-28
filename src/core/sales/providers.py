from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from core.integrations.enums import IntegrationProvider
from core.sales.enums import PaymentMethod
from core.shared.db import UUIDv7


class ProviderOutcomeState(StrEnum):
    """Transport-neutral provider operation states."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class ProviderErrorKind(StrEnum):
    """Provider-neutral failure categories used by financial orchestration."""

    OUTCOME_UNKNOWN = "outcome_unknown"
    DEFINITIVE_FAILURE = "definitive_failure"
    TEMPORARY_STATUS_QUERY_FAILURE = "temporary_status_query_failure"
    UNSUPPORTED_OPERATION = "unsupported_operation"


class ProviderCallError(Exception):
    """Sanitized provider failure safe to persist and expose to operators."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        outcome_unknown: bool | None = None,
        kind: ProviderErrorKind | None = None,
    ) -> None:
        """Create an error without secret headers or raw payloads."""
        if kind is None:
            if outcome_unknown is None:
                raise TypeError("ProviderCallError requires outcome_unknown or kind.")
            kind = (
                ProviderErrorKind.OUTCOME_UNKNOWN
                if outcome_unknown
                else ProviderErrorKind.DEFINITIVE_FAILURE
            )
        self.code = code[:128]
        self.kind = kind
        self.outcome_unknown = kind is ProviderErrorKind.OUTCOME_UNKNOWN
        super().__init__(message[:1000])

    @classmethod
    def temporary_status_query(cls, code: str, message: str) -> ProviderCallError:
        """Create a retry-safe error for a non-mutating provider status read."""
        return cls(code, message, kind=ProviderErrorKind.TEMPORARY_STATUS_QUERY_FAILURE)

    @classmethod
    def unsupported(cls, operation: str) -> ProviderCallError:
        """Describe an intentionally unsupported optional provider operation."""
        return cls(
            "unsupported_operation",
            f"Provider operation is not supported: {operation}.",
            kind=ProviderErrorKind.UNSUPPORTED_OPERATION,
        )


@dataclass(frozen=True)
class PaymentRequest:
    """Frozen acquiring command expressed only in Core concepts."""

    sale_id: UUIDv7
    attempt_id: UUIDv7
    idempotency_key: str
    amount: Decimal
    currency: str
    method: PaymentMethod


@dataclass(frozen=True)
class FiscalLine:
    """One immutable SaleItem snapshot mapped into a fiscal receipt."""

    item_id: UUIDv7
    allocation_index: int
    variant_id: UUIDv7 | None
    name: str
    sku: str | None
    barcode: str | None
    quantity: int
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class FiscalRequest:
    """Itemized fiscal command tied to confirmed payment evidence."""

    sale_id: UUIDv7
    fiscalization_id: UUIDv7
    idempotency_key: str
    amount: Decimal
    currency: str
    payment_method: PaymentMethod
    lines: tuple[FiscalLine, ...]
    payment_evidence: object | None


@dataclass(frozen=True)
class ProviderOperation:
    """Safe result of starting or polling one provider operation."""

    state: ProviderOutcomeState
    external_id: str | None = None
    external_reference: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    fiscal_evidence: object | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ProviderHealth:
    """Careful cloud/API signal that does not overclaim physical device readiness."""

    reachable: bool
    checked_at: str
    message: str
    physical_device_ready: bool | None = None


class PaymentProvider(Protocol):
    """Generic acquiring boundary used by Sales checkout orchestration."""

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Start one idempotently guarded payment operation."""

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Read authoritative provider payment state without creating a charge."""

    def request_payment_cancellation(
        self, external_id: str, request: PaymentRequest
    ) -> None:
        """Request device-command cancellation without claiming a bank reversal."""

    def check_health(self) -> ProviderHealth:
        """Check provider API/configuration without claiming terminal readiness."""


class FiscalProvider(Protocol):
    """Generic fiscal receipt boundary used after confirmed payment."""

    def find_fiscalization(self, request: FiscalRequest) -> ProviderOperation | None:
        """Find an already-created receipt without starting another fiscal operation."""

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Start one itemized fiscal operation."""

    def get_fiscalization(self, external_id: str, request: FiscalRequest) -> ProviderOperation:
        """Read authoritative fiscal state without creating another receipt."""

    def check_health(self) -> ProviderHealth:
        """Check provider API/configuration without mutating fiscal state."""


@dataclass
class CheckoutProviderBinding:
    """Resolved, independently selectable payment and fiscal capabilities.

    The original fields describe the payment selection and stay intact for
    compatibility.  Fiscal-specific fields may point at another Integration;
    when omitted they deliberately fall back to the payment selection used by
    all historical AQSI records.
    """

    integration_id: UUIDv7
    integration_name: str
    provider: IntegrationProvider
    provider_display_name: str
    payment_display_name: str
    payment: PaymentProvider | None
    fiscal: FiscalProvider | None
    close: Callable[[], None] | None = None
    fiscal_integration_id: UUIDv7 | None = None
    fiscal_integration_name: str | None = None
    fiscal_provider_kind: IntegrationProvider | None = None
    fiscal_provider_display_name: str | None = None

    @property
    def resolved_fiscal_integration_id(self) -> UUIDv7:
        """Return the selected fiscal Integration, including legacy fallback."""
        return self.fiscal_integration_id or self.integration_id

    @property
    def resolved_fiscal_integration_name(self) -> str:
        """Return the selected fiscal Integration name."""
        return self.fiscal_integration_name or self.integration_name

    @property
    def resolved_fiscal_provider(self) -> IntegrationProvider:
        """Return the selected fiscal provider kind."""
        return self.fiscal_provider_kind or self.provider

    @property
    def resolved_fiscal_provider_display_name(self) -> str:
        """Return the selected fiscal provider display name."""
        return self.fiscal_provider_display_name or self.provider_display_name

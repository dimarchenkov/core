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


class ProviderCallError(Exception):
    """Sanitized provider failure safe to persist and expose to operators."""

    def __init__(self, code: str, message: str, *, outcome_unknown: bool) -> None:
        """Create an error without secret headers or raw payloads."""
        self.code = code[:128]
        self.outcome_unknown = outcome_unknown
        super().__init__(message[:1000])


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


class PaymentProvider(Protocol):
    """Generic acquiring boundary used by Sales checkout orchestration."""

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Start one idempotently guarded payment operation."""

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Read authoritative provider payment state without creating a charge."""


class FiscalProvider(Protocol):
    """Generic fiscal receipt boundary used after confirmed payment."""

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Start one itemized fiscal operation."""

    def get_fiscalization(self, external_id: str, request: FiscalRequest) -> ProviderOperation:
        """Read authoritative fiscal state without creating another receipt."""


@dataclass
class CheckoutProviderBinding:
    """Resolved Integration plus its independent payment/fiscal capabilities."""

    integration_id: UUIDv7
    integration_name: str
    provider: IntegrationProvider
    provider_display_name: str
    payment_display_name: str
    payment: PaymentProvider
    fiscal: FiscalProvider
    close: Callable[[], None] | None = None

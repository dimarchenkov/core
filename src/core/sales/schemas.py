from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict, Field, computed_field

from core.integrations.enums import IntegrationProvider
from core.sales.enums import (
    FiscalizationStatus,
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


class PaymentAttemptRead(PydanticBaseModel):
    """Safe acquiring history without card data or raw provider payloads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    integration_id: UUIDv7 | None
    provider: IntegrationProvider | None
    payment_method: PaymentMethod
    requested_amount: Decimal
    currency: str
    external_id: str | None
    status: PaymentStatus
    attempt_number: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class FiscalizationRead(PydanticBaseModel):
    """Safe fiscal obligation state separate from acquiring."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    integration_id: UUIDv7
    provider: IntegrationProvider
    external_id: str | None
    external_receipt_id: str | None
    status: FiscalizationStatus
    attempt_count: int
    fiscal_amount: Decimal
    currency: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


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
    acquiring_label: str | None = None
    payment_methods: list[PaymentMethod] = Field(
        default_factory=lambda: [PaymentMethod.CASH, PaymentMethod.CARD]
    )
    stock_warnings: list[CheckoutStockWarningRead] = Field(default_factory=list)


class CheckoutStartRequest(PydanticBaseModel):
    """Operator-confirmed tender choice for a new payment fact."""

    model_config = ConfigDict(extra="forbid")

    payment_method: PaymentMethod = PaymentMethod.CARD

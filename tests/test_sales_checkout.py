from __future__ import annotations

import inspect
import json
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import core.sales.checkout as checkout_module
import core.sales.models as sales_models_module
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.config import Settings
from core.identity.models import User
from core.integrations.aqsi.checkout import AqsiCheckoutAdapter
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.checkout import (
    CheckoutIntegrationUnavailableError,
    CheckoutProviderFactory,
)
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.integrations.service import IntegrationConfigurationError, IntegrationService
from core.inventory.enums import MovementType
from core.inventory.models import StockMovement
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.sales.checkout import CheckoutService, CheckoutStateError
from core.sales.enums import (
    CheckoutPaymentOption,
    FiscalizationStatus,
    PaymentMethod,
    PaymentStatus,
    SaleItemSource,
    SaleStatus,
)
from core.sales.models import Sale
from core.sales.providers import (
    CheckoutProviderBinding,
    FiscalLine,
    FiscalRequest,
    PaymentRequest,
    ProviderCallError,
    ProviderOperation,
    ProviderOutcomeState,
)
from core.sales.schemas import CheckoutStartRequest
from core.sales.service import SaleNotDraftError, SaleService
from core.shared.db import Base
from core.shared.db.types import generate_uuid_v7


class FakeCheckoutProvider:
    """Deterministic generic provider used without AQSI/network side effects."""

    def __init__(self) -> None:
        """Initialize pending provider outcomes and call capture lists."""
        self.payment_initiations: list[PaymentRequest] = []
        self.fiscal_initiations: list[FiscalRequest] = []
        self.payment_state = ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id="payment-operation-1",
        )
        self.fiscal_state = ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id="fiscal-operation-1",
        )
        self.payment_error: ProviderCallError | None = None
        self.fiscal_error: ProviderCallError | None = None

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Record one acquiring initiation."""
        self.payment_initiations.append(request)
        if self.payment_error:
            raise self.payment_error
        return self.payment_state

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Return the configured authoritative acquiring state."""
        del external_id, request
        if self.payment_error:
            raise self.payment_error
        return self.payment_state

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Record one itemized receipt initiation."""
        self.fiscal_initiations.append(request)
        if self.fiscal_error:
            raise self.fiscal_error
        result = self.fiscal_state
        if result.external_id == "fiscal-operation-1" and len(self.fiscal_initiations) > 1:
            return ProviderOperation(result.state, external_id="fiscal-operation-2")
        return result

    def get_fiscalization(self, external_id: str, request: FiscalRequest) -> ProviderOperation:
        """Return the configured authoritative receipt state."""
        del external_id, request
        if self.fiscal_error:
            raise self.fiscal_error
        return self.fiscal_state


@pytest.fixture
def session() -> Generator[Session]:
    """Provide complete SQLite metadata for checkout lifecycle tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def checkout_setup(
    session: Session,
) -> tuple[User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding]:
    """Create a priced Variant and enabled generic checkout Integration."""
    user = User(email="checkout@example.com", full_name="Checkout", password_hash="unused")
    category = Category(title="Checkout", slug="checkout")
    product = CatalogProduct(title="Блокнот", slug="checkout-notebook", category=category)
    variant = CatalogVariant(
        product=product,
        title="Красный",
        sku="CHECKOUT-001",
        barcode="4601234567893",
        attributes={},
    )
    integration = Integration(
        provider=IntegrationProvider.AQSI,
        name="AQSI — касса Трёхполье",
        enabled=True,
        configuration={},
    )
    session.add_all([user, variant, integration])
    session.flush()
    session.add(
        Price(
            variant_id=variant.id,
            price_type=PriceType.RETAIL,
            amount=Decimal("12.34"),
            currency="RUB",
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()
    provider = FakeCheckoutProvider()
    binding = CheckoutProviderBinding(
        integration_id=integration.id,
        integration_name=integration.name,
        provider=IntegrationProvider.AQSI,
        provider_display_name="AQSI",
        payment_display_name="Карта / QR",
        payment=provider,
        fiscal=provider,
    )
    return user, variant, integration, provider, binding


def _sale_with_item(
    session: Session, user: User, variant: CatalogVariant, *, quantity: int = 1
) -> Sale:
    """Create one priced DRAFT with the requested frozen quantity."""
    sales = SaleService(session)
    sale = sales.create_sale(owner_id=user.id)
    for _ in range(quantity):
        sale = sales.add_variant(sale.id, variant.id, owner_id=user.id)
    return sale


def _succeeded_payment(external_id: str = "payment-operation-1") -> ProviderOperation:
    """Return a confirmed card payment carrying ephemeral fiscal Slip evidence."""
    return ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id=external_id,
        external_reference="slip-1",
        fiscal_evidence={
            "id": "slip-1",
            "content": {"type": "purchase", "amount": 2468},
        },
    )


def test_checkout_freezes_exact_total_and_duplicate_start_is_idempotent(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """DRAFT transitions once and repeated commands cannot create another charge."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    checkout = CheckoutService(session, binding)

    started = checkout.start(sale.id, owner_id=user.id)
    repeated = checkout.start(sale.id, owner_id=user.id)

    assert started.status is repeated.status is SaleStatus.PAYMENT_PENDING
    assert len(provider.payment_initiations) == 1
    assert provider.payment_initiations[0].amount == Decimal("24.68")
    assert started.payments[0].requested_amount == started.total_amount
    assert started.payments[0].status is PaymentStatus.PENDING
    with pytest.raises(SaleNotDraftError):
        SaleService(session).add_variant(sale.id, variant.id, owner_id=user.id)


def test_unknown_payment_blocks_blind_second_charge(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A lost response remains UNKNOWN and normal checkout replay performs no call."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    provider.payment_error = ProviderCallError(
        "timeout", "Provider response timed out.", outcome_unknown=True
    )
    checkout = CheckoutService(session, binding)

    unknown = checkout.start(sale.id, owner_id=user.id)
    replayed = checkout.start(sale.id, owner_id=user.id)

    assert unknown.status is replayed.status is SaleStatus.PAYMENT_PENDING
    assert unknown.payments[0].status is PaymentStatus.UNKNOWN
    assert len(provider.payment_initiations) == 1


def test_cash_with_receipt_skips_acquiring_and_is_idempotent(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Cash persists once, never calls PaymentProvider, and starts a separate receipt."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    checkout = CheckoutService(session, binding)

    confirmed = checkout.start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    repeated = checkout.start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )

    assert provider.payment_initiations == []
    assert len(confirmed.payments) == len(repeated.payments) == 1
    payment = repeated.payments[0]
    assert payment.payment_method is PaymentMethod.CASH
    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.provider is None
    assert payment.integration_id is None
    assert payment.fiscalization_required is True
    assert payment.requested_amount == repeated.total_amount == Decimal("24.68")
    assert repeated.status is SaleStatus.FISCALIZATION_PENDING
    assert repeated.fiscalization is not None
    assert repeated.fiscalization.status is FiscalizationStatus.PENDING
    assert len(provider.fiscal_initiations) == 1
    assert provider.fiscal_initiations[0].payment_method is PaymentMethod.CASH
    assert provider.fiscal_initiations[0].payment_evidence is None
    assert session.query(StockMovement).count() == 1

    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fiscal-operation-1",
        external_reference="cash-receipt-1",
    )
    completed = checkout.progress(sale.id, owner_id=user.id)

    assert completed.status is SaleStatus.COMPLETED
    assert completed.fiscalization.status is FiscalizationStatus.SUCCEEDED
    assert session.query(StockMovement).count() == 1


def test_cash_without_receipt_completes_without_any_provider_call(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Explicit no-receipt cash records SKIPPED and exact-once Catalog Inventory."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    sale = SaleService(session).add_manual_item(
        sale.id,
        "Разовая услуга",
        Decimal("5.00"),
        1,
        owner_id=user.id,
    )
    checkout = CheckoutService(session, binding)

    completed = checkout.start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITHOUT_RECEIPT,
    )
    repeated = checkout.start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITHOUT_RECEIPT,
    )

    assert completed.status is repeated.status is SaleStatus.COMPLETED
    assert len(repeated.payments) == 1
    assert repeated.payments[0].payment_method is PaymentMethod.CASH
    assert repeated.payments[0].status is PaymentStatus.SUCCEEDED
    assert repeated.payments[0].fiscalization_required is False
    assert repeated.payments[0].requested_amount == repeated.total_amount
    assert repeated.fiscalization is not None
    assert repeated.fiscalization.status is FiscalizationStatus.SKIPPED
    assert repeated.fiscalization.provider is None
    assert repeated.fiscalization.integration_id is None
    assert provider.payment_initiations == []
    assert provider.fiscal_initiations == []
    movements = session.query(StockMovement).all()
    assert len(movements) == 1
    assert movements[0].quantity_delta == Decimal("-2")


def test_card_checkout_has_no_supported_skip_fiscalization_option(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """The public command has no card-without-receipt state and CARD remains required."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)

    started = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CARD,
    )

    assert started.payments[0].payment_method is PaymentMethod.CARD
    assert started.payments[0].fiscalization_required is True
    assert len(provider.payment_initiations) == 1
    with pytest.raises(ValueError):
        CheckoutStartRequest.model_validate({"payment_option": "card_without_receipt"})


def test_definite_payment_failure_allows_new_attempt_only_via_explicit_retry(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Failed acquiring history is retained while retry creates a new identity."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.FAILED,
        error_code="declined",
        error_message="Card declined.",
    )
    checkout = CheckoutService(session, binding)

    failed = checkout.start(sale.id, owner_id=user.id)
    assert failed.status is SaleStatus.DRAFT
    assert failed.payments[0].status is PaymentStatus.FAILED
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="payment-operation-2"
    )
    retried = checkout.retry_payment(sale.id, owner_id=user.id)

    assert retried.status is SaleStatus.PAYMENT_PENDING
    assert [attempt.status for attempt in retried.payments] == [
        PaymentStatus.FAILED,
        PaymentStatus.PENDING,
    ]
    assert len({attempt.idempotency_key for attempt in retried.payments}) == 2
    assert len(provider.payment_initiations) == 2


def test_terminal_cancellation_returns_editable_draft_and_retry_uses_new_attempt(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Definitive terminal cancel is safe, editable and distinct from UNKNOWN."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.CANCELED,
        external_id="payment-operation-1",
        error_code="terminal_canceled",
    )

    canceled = CheckoutService(session, binding).start(sale.id, owner_id=user.id)

    assert canceled.status is SaleStatus.DRAFT
    assert canceled.payments[0].status is PaymentStatus.CANCELED
    assert session.query(StockMovement).count() == 0
    assert canceled.fiscalization is None
    with pytest.raises(CheckoutStateError):
        CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    assert len(provider.payment_initiations) == 1
    edited = SaleService(session).set_discount(
        sale.id, Decimal("5.00"), owner_id=user.id
    )
    assert edited.discount_value == Decimal("5.00")
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="payment-operation-2"
    )

    retried = CheckoutService(session, binding).retry_payment(sale.id, owner_id=user.id)

    assert retried.status is SaleStatus.PAYMENT_PENDING
    assert [attempt.status for attempt in retried.payments] == [
        PaymentStatus.CANCELED,
        PaymentStatus.PENDING,
    ]
    assert len(provider.payment_initiations) == 2


def test_payment_success_posts_negative_inventory_once_and_uses_snapshots(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Confirmed payment creates one negative ledger row before itemized fiscalization."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()

    fiscal_pending = checkout.progress(sale.id, owner_id=user.id)
    checkout.progress(sale.id, owner_id=user.id)
    movements = session.query(StockMovement).all()
    request = provider.fiscal_initiations[0]

    assert fiscal_pending.status is SaleStatus.FISCALIZATION_PENDING
    assert fiscal_pending.inventory_posted_at is not None
    assert len(movements) == 1
    assert movements[0].movement_type is MovementType.SALE
    assert movements[0].quantity_delta == Decimal("-2")
    assert movements[0].source_id == sale.id
    assert len(provider.fiscal_initiations) == 1
    assert request.amount == Decimal("24.68")
    assert request.lines[0].name == "Блокнот · Красный"
    assert request.lines[0].unit_price == Decimal("12.34")
    assert request.lines[0].quantity == 2


def test_discount_and_manual_item_are_frozen_for_fiscalization_but_not_inventory(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Discounted manual lines reach the receipt while only Catalog lines affect stock."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    sale = SaleService(session).add_manual_item(
        sale.id,
        "Разовая услуга",
        Decimal("5.55"),
        1,
        owner_id=user.id,
    )
    sale = SaleService(session).set_discount(
        sale.id, Decimal("10.00"), owner_id=user.id
    )
    assert sale.total_amount == Decimal("27.21")
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    with pytest.raises(SaleNotDraftError):
        SaleService(session).set_discount(sale.id, Decimal("5.00"), owner_id=user.id)
    provider.payment_state = _succeeded_payment()

    pending = checkout.progress(sale.id, owner_id=user.id)
    request = provider.fiscal_initiations[0]

    assert pending.status is SaleStatus.FISCALIZATION_PENDING
    assert session.query(StockMovement).count() == 1
    assert {line.name for line in request.lines} == {"Блокнот · Красный", "Разовая услуга"}
    assert sum(line.line_total for line in request.lines) == pending.total_amount
    manual = next(item for item in pending.items if item.source is SaleItemSource.MANUAL)
    assert manual.variant_id is None


def test_fiscal_success_completes_and_recovery_does_not_duplicate_effects(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Reload-style progress reaches COMPLETED with one charge, receipt and ledger row."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    checkout.progress(sale.id, owner_id=user.id)
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fiscal-operation-1",
        external_reference="receipt-1",
    )

    completed = CheckoutService(session, binding).progress(sale.id, owner_id=user.id)
    recovered = CheckoutService(session, binding).progress(sale.id, owner_id=user.id)

    assert completed.status is recovered.status is SaleStatus.COMPLETED
    assert completed.fiscalization.status is FiscalizationStatus.SUCCEEDED
    assert completed.fiscalization.external_receipt_id == "receipt-1"
    assert len(provider.payment_initiations) == 1
    assert len(provider.fiscal_initiations) == 1
    assert session.query(StockMovement).count() == 1
    with pytest.raises(SaleNotDraftError):
        SaleService(session).cancel_sale(sale.id, owner_id=user.id)
    assert SaleService(session).list_draft_sales(owner_id=user.id) == []


def test_fiscal_failure_never_repeats_payment_and_explicit_retry_is_separate(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A paid Sale stays paid while a definite receipt failure is safely retried."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    checkout.progress(sale.id, owner_id=user.id)
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.FAILED,
        external_id="fiscal-operation-1",
        error_code="printer_error",
    )
    failed = checkout.progress(sale.id, owner_id=user.id)
    assert failed.status is SaleStatus.FISCALIZATION_FAILED
    assert failed.payments[0].status is PaymentStatus.SUCCEEDED
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="fiscal-operation-1"
    )

    retried = checkout.retry_fiscalization(sale.id, owner_id=user.id)

    assert retried.status is SaleStatus.FISCALIZATION_PENDING
    assert retried.fiscalization.attempt_count == 2
    assert len(provider.payment_initiations) == 1
    assert len(provider.fiscal_initiations) == 2
    assert session.query(StockMovement).count() == 1


def test_other_draft_sale_remains_untouched_during_checkout(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Only the selected Sale freezes; another cart remains a valid scanner target."""
    user, variant, _, _, binding = checkout_setup
    selected = _sale_with_item(session, user, variant)
    other = SaleService(session).create_sale(owner_id=user.id)

    CheckoutService(session, binding).start(selected.id, owner_id=user.id)
    changed = SaleService(session).add_variant(other.id, variant.id, owner_id=user.id)

    assert selected.status is SaleStatus.PAYMENT_PENDING
    assert changed.status is SaleStatus.DRAFT
    assert {sale.id for sale in SaleService(session).list_draft_sales(owner_id=user.id)} == {
        other.id
    }


def test_sales_domain_models_and_orchestrator_have_no_aqsi_dependency() -> None:
    """Sales uses provider ports; AQSI remains an integration adapter."""
    source = inspect.getsource(checkout_module) + inspect.getsource(sales_models_module)

    assert "core.integrations.aqsi" not in source


def test_sales_domain_does_not_know_aqsi_acquiring_modes() -> None:
    """Provider enum strings remain confined to AQSI integration code."""
    source = inspect.getsource(checkout_module) + inspect.getsource(sales_models_module)

    assert "card_only" not in source
    assert "sbp_with_card" not in source
    assert "sbp_only" not in source
    assert "Aqsi" not in source


def test_checkout_factory_never_falls_back_to_legacy_environment_secret(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A configured env key cannot bypass a missing IntegrationCredential."""
    _, _, _, _, _ = checkout_setup
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_enabled=True,
        aqsi_api_key=SecretStr("legacy-must-not-be-used"),
    )

    with pytest.raises(CheckoutIntegrationUnavailableError, match="ключ"):
        CheckoutProviderFactory(session, settings).for_new_checkout()


class RecordingAqsiClient:
    """Minimal AQSI client double capturing direct checkout payloads."""

    def __init__(self) -> None:
        """Initialize recorded payloads and authoritative operation response."""
        self.purchase_payload: dict[str, object] | None = None
        self.receipt_payload: dict[str, object] | None = None
        self.operation: dict[str, object] = {}

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Capture acquiring payload."""
        self.purchase_payload = payload
        return {"operationId": "payment-op"}

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Return configured operation state."""
        del operation_id
        return self.operation

    def create_receipt(self, payload: dict[str, object]) -> dict[str, object]:
        """Capture itemized fiscal payload."""
        self.receipt_payload = payload
        return {"operationId": "receipt-op"}


def test_aqsi_adapter_maps_exact_kopecks_and_itemized_sale_snapshots() -> None:
    """AQSI receives exact card amount and frozen itemized receipt values."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
    )
    client = RecordingAqsiClient()
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))
    sale_id = generate_uuid_v7()
    attempt_id = generate_uuid_v7()
    payment = PaymentRequest(
        sale_id=sale_id,
        attempt_id=attempt_id,
        idempotency_key="payment-key",
        amount=Decimal("24.68"),
        currency="RUB",
        method=PaymentMethod.CARD,
    )

    adapter.initiate_payment(payment)
    slip = {"id": "slip-1", "content": {"type": "purchase", "amount": 2468}}
    client.operation = {"status": "Completed", "result": json.dumps(slip)}
    confirmed = adapter.get_payment("payment-op", payment)
    fiscal = FiscalRequest(
        sale_id=sale_id,
        fiscalization_id=generate_uuid_v7(),
        idempotency_key="fiscal-key",
        amount=Decimal("24.68"),
        currency="RUB",
        payment_method=PaymentMethod.CARD,
        lines=(
                FiscalLine(
                    item_id=generate_uuid_v7(),
                    allocation_index=0,
                    variant_id=generate_uuid_v7(),
                name="Блокнот · Красный",
                sku="CHECKOUT-001",
                barcode="4601234567893",
                quantity=2,
                unit_price=Decimal("12.34"),
                line_total=Decimal("24.68"),
            ),
        ),
        payment_evidence=confirmed.fiscal_evidence,
    )
    adapter.initiate_fiscalization(fiscal)

    assert client.purchase_payload == {
        "deviceId": 748887,
        "ttlMillis": 120000,
        "amount": 2468,
        "mode": "sbp_with_card",
        "printCount": 0,
    }
    assert client.receipt_payload is not None
    assert client.receipt_payload["payments"] == [{"type": 1, "amount": 2468, "slip": slip}]
    position = client.receipt_payload["positions"][0]
    assert position["info"]["name"] == "Блокнот · Красный"
    assert position["info"]["baseQuantity"] == "2"
    assert position["info"]["finalPrice"] == 1234


@pytest.mark.parametrize("mode", ["card_only", "sbp_only"])
def test_aqsi_adapter_maps_configured_acquiring_mode(mode: str) -> None:
    """AQSI-specific mode configuration is mapped into the purchase request."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
        aqsi_acquiring_mode=mode,
    )
    client = RecordingAqsiClient()
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))

    adapter.initiate_payment(
        PaymentRequest(
            sale_id=generate_uuid_v7(),
            attempt_id=generate_uuid_v7(),
            idempotency_key="payment-key",
            amount=Decimal("10.00"),
            currency="RUB",
            method=PaymentMethod.CARD,
        )
    )

    assert client.purchase_payload is not None
    assert client.purchase_payload["mode"] == mode


def test_invalid_aqsi_acquiring_mode_is_rejected() -> None:
    """Integration settings accept only AQSI's explicitly supported safe modes."""
    with pytest.raises(IntegrationConfigurationError, match="acquiring_mode"):
        IntegrationService._validate_configuration(
            IntegrationProvider.AQSI,
            {"acquiring_mode": "unsupported"},
        )


def test_aqsi_cash_receipt_uses_cash_tender_without_slip() -> None:
    """Cash fiscalization is an explicit AQSI receipt payment, not skipped acquiring."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
    )
    client = RecordingAqsiClient()
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))
    fiscal = FiscalRequest(
        sale_id=generate_uuid_v7(),
        fiscalization_id=generate_uuid_v7(),
        idempotency_key="cash-fiscal-key",
        amount=Decimal("10.00"),
        currency="RUB",
        payment_method=PaymentMethod.CASH,
        lines=(
            FiscalLine(
                item_id=generate_uuid_v7(),
                allocation_index=0,
                variant_id=None,
                name="Тестовый товар",
                sku=None,
                barcode=None,
                quantity=1,
                unit_price=Decimal("10.00"),
                line_total=Decimal("10.00"),
            ),
        ),
        payment_evidence=None,
    )

    adapter.initiate_fiscalization(fiscal)

    assert client.purchase_payload is None
    assert client.receipt_payload is not None
    assert client.receipt_payload["payments"] == [{"type": 0, "amount": 1000}]


def test_aqsi_adapter_maps_terminal_canceled_separately_from_unknown() -> None:
    """An authoritative AQSI Canceled state is safe to retry and never UNKNOWN."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
    )
    client = RecordingAqsiClient()
    client.operation = {"status": "Canceled", "message": "Canceled on terminal"}
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))
    request = PaymentRequest(
        sale_id=generate_uuid_v7(),
        attempt_id=generate_uuid_v7(),
        idempotency_key="payment-key",
        amount=Decimal("10.00"),
        currency="RUB",
        method=PaymentMethod.CARD,
    )

    result = adapter.get_payment("payment-op", request)

    assert result.state is ProviderOutcomeState.CANCELED
    assert result.error_code == "aqsi_canceled"

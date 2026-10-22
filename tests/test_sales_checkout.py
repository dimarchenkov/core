from __future__ import annotations

import inspect
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import core.main  # noqa: F401 -- register all models required by route-level read checks
import core.sales.checkout as checkout_module
import core.sales.fiscalization_jobs as fiscal_jobs_module
import core.sales.models as sales_models_module
import core.sales.payment_jobs as payment_jobs_module
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
    FiscalizationJobStatus,
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
from core.sales.repository import SaleRepository
from core.sales.routes import list_checkout_operations
from core.sales.schemas import CheckoutStartRequest, SaleRead
from core.sales.service import SaleNotDraftError, SaleService
from core.shared.db import Base
from core.shared.db.types import generate_uuid_v7


class FakeCheckoutProvider:
    """Deterministic generic provider used without AQSI/network side effects."""

    def __init__(self) -> None:
        """Initialize pending provider outcomes and call capture lists."""
        self.payment_initiations: list[PaymentRequest] = []
        self.payment_polls = 0
        self.fiscal_initiations: list[FiscalRequest] = []
        self.fiscal_lookups: list[FiscalRequest] = []
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
        self.fiscal_lookup_error: ProviderCallError | None = None
        self.existing_fiscalization: ProviderOperation | None = None

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Record one acquiring initiation."""
        self.payment_initiations.append(request)
        if self.payment_error:
            raise self.payment_error
        return self.payment_state

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Return the configured authoritative acquiring state."""
        del external_id, request
        self.payment_polls += 1
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

    def find_fiscalization(self, request: FiscalRequest) -> ProviderOperation | None:
        """Return an already-created receipt without initiating another one."""
        self.fiscal_lookups.append(request)
        if self.fiscal_lookup_error:
            raise self.fiscal_lookup_error
        return self.existing_fiscalization

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
    checkout.process_payment(sale.id, owner_id=user.id)
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

    checkout.start(sale.id, owner_id=user.id)
    unknown = checkout.process_payment(sale.id, owner_id=user.id)
    replayed = checkout.start(sale.id, owner_id=user.id)
    restarted_worker_step = checkout.process_payment(sale.id, owner_id=user.id)

    assert (
        unknown.status
        is replayed.status
        is restarted_worker_step.status
        is SaleStatus.PAYMENT_PENDING
    )
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
    assert repeated.fiscalization_job is not None
    assert repeated.fiscalization_job.status.value == "queued"
    checkout.process_fiscalization(sale.id, owner_id=user.id)
    assert len(provider.fiscal_initiations) == 1
    assert provider.fiscal_initiations[0].payment_method is PaymentMethod.CASH
    assert provider.fiscal_initiations[0].payment_evidence is None
    assert session.query(StockMovement).count() == 1

    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fiscal-operation-1",
        external_reference="cash-receipt-1",
    )
    completed = checkout.process_fiscalization(sale.id, owner_id=user.id)

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


def test_cash_receipt_unknown_without_operation_id_is_never_recreated(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Cash has no receipt lookup, so an ambiguous POST must stop for manual review."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    provider.fiscal_error = ProviderCallError(
        "http_500", "AQSI could not return the receipt result.", outcome_unknown=True
    )

    unknown = checkout.process_fiscalization(sale.id, owner_id=user.id)
    repeated = checkout.process_fiscalization(sale.id, owner_id=user.id)

    assert unknown.status is repeated.status is SaleStatus.FISCALIZATION_PENDING
    assert repeated.fiscalization.status is FiscalizationStatus.UNKNOWN
    assert repeated.fiscalization.external_id is None
    assert len(provider.fiscal_initiations) == 1


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
    started = CheckoutService(session, binding).process_payment(sale.id, owner_id=user.id)

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

    checkout.start(sale.id, owner_id=user.id)
    failed = checkout.process_payment(sale.id, owner_id=user.id)
    assert failed.status is SaleStatus.DRAFT
    assert failed.payments[0].status is PaymentStatus.FAILED
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="payment-operation-2"
    )
    retried = checkout.retry_payment(sale.id, owner_id=user.id)
    retried = checkout.process_payment(sale.id, owner_id=user.id)

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

    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    canceled = checkout.process_payment(sale.id, owner_id=user.id)

    assert canceled.status is SaleStatus.DRAFT
    assert canceled.payments[0].status is PaymentStatus.CANCELED
    assert session.query(StockMovement).count() == 0
    assert canceled.fiscalization is None
    with pytest.raises(CheckoutStateError):
        CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    assert len(provider.payment_initiations) == 1
    edited = SaleService(session).set_discount(sale.id, Decimal("5.00"), owner_id=user.id)
    assert edited.discount_value == Decimal("5.00")
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="payment-operation-2"
    )

    retried = CheckoutService(session, binding).retry_payment(sale.id, owner_id=user.id)
    retried = CheckoutService(session, binding).process_payment(sale.id, owner_id=user.id)

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

    fiscal_pending = checkout.process_payment(sale.id, owner_id=user.id)
    checkout.process_fiscalization(sale.id, owner_id=user.id)
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
    sale = SaleService(session).set_discount(sale.id, Decimal("10.00"), owner_id=user.id)
    assert sale.total_amount == Decimal("27.21")
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    with pytest.raises(SaleNotDraftError):
        SaleService(session).set_discount(sale.id, Decimal("5.00"), owner_id=user.id)
    provider.payment_state = _succeeded_payment()

    pending = checkout.process_payment(sale.id, owner_id=user.id)
    checkout.process_fiscalization(sale.id, owner_id=user.id)
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
    checkout.process_payment(sale.id, owner_id=user.id)
    checkout.process_fiscalization(sale.id, owner_id=user.id)
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fiscal-operation-1",
        external_reference="receipt-1",
    )

    completed = CheckoutService(session, binding).process_fiscalization(sale.id, owner_id=user.id)
    recovered = CheckoutService(session, binding).process_fiscalization(sale.id, owner_id=user.id)

    assert completed.status is recovered.status is SaleStatus.COMPLETED
    assert completed.fiscalization.status is FiscalizationStatus.SUCCEEDED
    assert completed.fiscalization.external_receipt_id == "receipt-1"
    assert len(provider.payment_initiations) == 1
    assert provider.payment_polls == 1
    assert len(provider.fiscal_initiations) == 1
    assert session.query(StockMovement).count() == 1
    with pytest.raises(SaleNotDraftError):
        SaleService(session).cancel_sale(sale.id, owner_id=user.id)
    assert SaleService(session).list_draft_sales(owner_id=user.id) == []


def test_existing_receipt_is_linked_without_creating_another_fiscal_operation(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A receipt found by payment evidence completes the Sale without another POST."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    provider.existing_fiscalization = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_reference="existing-receipt-1",
        metadata={"recovered_existing_receipt": True},
    )
    checkout.process_payment(sale.id, owner_id=user.id)

    completed = checkout.process_fiscalization(sale.id, owner_id=user.id)
    repeated = checkout.process_fiscalization(sale.id, owner_id=user.id)

    assert completed.status is repeated.status is SaleStatus.COMPLETED
    assert completed.fiscalization.status is FiscalizationStatus.SUCCEEDED
    assert completed.fiscalization.external_receipt_id == "existing-receipt-1"
    assert completed.fiscalization.provider_metadata["recovered_existing_receipt"] is True
    assert len(provider.payment_initiations) == 1
    assert len(provider.fiscal_lookups) == 1
    assert provider.fiscal_initiations == []
    assert session.query(StockMovement).count() == 1


def test_receipt_lookup_failure_blocks_fiscal_post_and_keeps_payment_successful(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """An inconclusive preflight never risks a duplicate receipt or payment."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    provider.fiscal_lookup_error = ProviderCallError(
        "receipt_lookup_failed", "Receipt lookup failed.", outcome_unknown=True
    )

    pending = checkout.process_payment(sale.id, owner_id=user.id)
    pending = checkout.process_fiscalization(sale.id, owner_id=user.id)
    repeated = checkout.process_fiscalization(sale.id, owner_id=user.id)

    assert pending.status is repeated.status is SaleStatus.FISCALIZATION_PENDING
    assert pending.payments[0].status is PaymentStatus.SUCCEEDED
    assert pending.fiscalization.status is FiscalizationStatus.UNKNOWN
    assert pending.fiscalization.error_code == "receipt_lookup_failed"
    assert len(provider.payment_initiations) == 1
    assert len(provider.fiscal_lookups) == 1
    assert provider.fiscal_initiations == []


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
    checkout.process_payment(sale.id, owner_id=user.id)
    checkout.process_fiscalization(sale.id, owner_id=user.id)
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.FAILED,
        external_id="fiscal-operation-1",
        error_code="printer_error",
    )
    failed = checkout.process_fiscalization(sale.id, owner_id=user.id)
    assert failed.status is SaleStatus.FISCALIZATION_FAILED
    assert failed.payments[0].status is PaymentStatus.SUCCEEDED
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.PENDING, external_id="fiscal-operation-1"
    )

    retried = checkout.retry_fiscalization(sale.id, owner_id=user.id)
    retried = checkout.process_fiscalization(sale.id, owner_id=user.id)

    assert retried.status is SaleStatus.FISCALIZATION_PENDING
    assert retried.fiscalization.attempt_count == 2
    assert len(provider.payment_initiations) == 1
    assert len(provider.fiscal_initiations) == 2
    assert session.query(StockMovement).count() == 1


def test_dismiss_failed_receipt_preserves_financial_facts_and_blocks_retries(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator dismissal hides follow-up, never fakes a receipt or reverses payment."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    checkout.process_payment(sale.id, owner_id=user.id)
    with pytest.raises(CheckoutStateError):
        CheckoutService(session, None).dismiss_failed_fiscalization(
            sale.id, owner_id=user.id, reason="Not actionable"
        )
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.FAILED, external_id="fiscal-operation-1", error_code="printer_error"
    )
    checkout.process_fiscalization(sale.id, owner_id=user.id)
    sale.fiscalization_job.status = FiscalizationJobStatus.FAILED
    session.commit()
    assert sale in SaleRepository(session).list_drafts(user.id)
    amounts = (sale.total_amount, sale.paid_at, sale.inventory_posted_at, sale.completed_at)
    calls = (
        len(provider.payment_initiations),
        len(provider.fiscal_initiations),
        provider.payment_polls,
    )

    local = CheckoutService(session, None)
    dismissed = local.dismiss_failed_fiscalization(
        sale.id, owner_id=user.id, reason="User decision"
    )
    audit = dict(dismissed.fiscalization.provider_metadata)
    local.dismiss_failed_fiscalization(sale.id, owner_id=user.id, reason="Second click")
    assert dismissed.fiscalization.provider_metadata == audit
    assert dismissed.status is SaleStatus.FISCALIZATION_FAILED
    assert dismissed.fiscalization.status is FiscalizationStatus.FAILED
    assert dismissed.payments[0].status is PaymentStatus.SUCCEEDED
    assert SaleRead.model_validate(dismissed).fiscalization.operator_dismissed
    assert amounts == (sale.total_amount, sale.paid_at, sale.inventory_posted_at, sale.completed_at)
    assert dismissed not in SaleRepository(session).list_drafts(user.id)
    assert dismissed in list_checkout_operations(
        session, user, state="all", date_from=None, date_to=None, limit=100
    )
    assert dismissed not in list_checkout_operations(
        session, user, state="failed", date_from=None, date_to=None, limit=100
    )
    with pytest.raises(CheckoutStateError, match="неактуальная"):
        checkout.retry_fiscalization(sale.id, owner_id=user.id)
    local.process_fiscalization(sale.id, owner_id=user.id)
    assert calls == (
        len(provider.payment_initiations),
        len(provider.fiscal_initiations),
        provider.payment_polls,
    )
    assert session.query(StockMovement).count() == 1
    assert sale.fiscalization_job.status is FiscalizationJobStatus.FAILED
    assert sale.fiscalization_job.next_retry_at is None
    # Even a stale queue entry cannot make the worker claim dismissed work.
    job_id = sale.fiscalization_job.id
    sale.fiscalization_job.status = FiscalizationJobStatus.QUEUED
    session.commit()
    engine = session.get_bind()
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", lambda: Session(engine))
    assert fiscal_jobs_module._claim_job(job_id) is None


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
        other.id,
        selected.id,
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
        self.operation_calls: list[str] = []
        self.existing_receipt: dict[str, object] | None = None
        self.receipt_lookups: list[tuple[str, datetime]] = []

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Capture acquiring payload."""
        self.purchase_payload = payload
        return {"operationId": "payment-op"}

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Return configured operation state."""
        self.operation_calls.append(operation_id)
        return self.operation

    def create_receipt(self, payload: dict[str, object]) -> dict[str, object]:
        """Capture itemized fiscal payload."""
        self.receipt_payload = payload
        return {"operationId": "receipt-op"}

    def find_receipt_by_slip(
        self, slip_id: str, *, acquired_at: datetime
    ) -> dict[str, object] | None:
        """Capture the non-mutating receipt lookup."""
        self.receipt_lookups.append((slip_id, acquired_at))
        return self.existing_receipt


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
    client.operation = {"status": "Pending"}
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
    initiated = adapter.initiate_fiscalization(fiscal)

    assert client.purchase_payload == {
        "deviceId": 748887,
        "ttlMillis": 120000,
        "amount": 2468,
        "mode": "sbp_with_card",
        "printCount": 0,
    }
    assert client.receipt_payload is not None
    assert initiated.state is ProviderOutcomeState.PENDING
    assert client.operation_calls == ["payment-op"]
    assert initiated.metadata["provider_status"] == "Accepted"
    assert "receipt_process_http_ms" in initiated.metadata
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


def test_aqsi_adapter_recovers_matching_fiscal_receipt_by_acquiring_slip() -> None:
    """AQSI receipt lookup maps verified fiscal facts without creating a receipt."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
    )
    client = RecordingAqsiClient()
    client.existing_receipt = {
        "id": "receipt-1",
        "isNonFiscal": False,
        "device": {"id": 748887},
        "info": {
            "sum": 1000,
            "docInfo": {
                "docNumber": 42,
                "fiscalStorageNumber": "9999078900012345",
                "docFiscalAttributeInt": 1234567890,
            },
        },
    }
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))
    request = FiscalRequest(
        sale_id=generate_uuid_v7(),
        fiscalization_id=generate_uuid_v7(),
        idempotency_key="fiscal-key",
        amount=Decimal("10.00"),
        currency="RUB",
        payment_method=PaymentMethod.CARD,
        lines=(),
        payment_evidence={
            "id": "slip-1",
            "content": {
                "type": "purchase",
                "amount": 1000,
                "dateTime": "2026-10-19T15:19:42+03:00",
            },
        },
    )

    found = adapter.find_fiscalization(request)

    assert found is not None
    assert found.state is ProviderOutcomeState.SUCCEEDED
    assert found.external_reference == "receipt-1"
    assert found.metadata["recovered_existing_receipt"] is True
    assert client.receipt_lookups[0][0] == "slip-1"
    assert client.receipt_payload is None


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


def test_durable_fiscalization_job_claim_prevents_second_worker(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payment approval and one claim are durable; a second worker cannot claim it."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    job = sale.fiscalization_job
    assert job is not None
    assert job.status.value == "queued"
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", factory)

    first = fiscal_jobs_module._claim_job(job.id)
    second = fiscal_jobs_module._claim_job(job.id)

    assert first is not None
    assert first[0] == sale.id
    assert second is None


def test_payment_request_only_queues_durable_worker_job(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """The HTTP command persists work but never owns the acquiring lifecycle."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)

    queued = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    observed = CheckoutService(session, binding).progress(sale.id, owner_id=user.id)

    assert queued.status is observed.status is SaleStatus.PAYMENT_PENDING
    assert queued.payments[0].job is not None
    assert queued.payments[0].job.status.value == "queued"
    assert provider.payment_initiations == []
    assert provider.payment_polls == 0


def test_durable_payment_job_claim_prevents_second_worker(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database claiming prevents duplicate acquiring execution."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    job = sale.payments[0].job
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)

    first = payment_jobs_module._claim_job(job.id)
    second = payment_jobs_module._claim_job(job.id)

    assert first is not None
    assert first[0] == sale.id
    assert second is None


def test_payment_worker_completes_and_atomically_creates_fiscal_job(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker approval persists payment and fiscal handoff without a browser poll."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, integration_id: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(payment_jobs_module, "enqueue_sale_fiscalization", lambda sale_id: True)

    result = payment_jobs_module.run_payment_job(str(sale.payments[0].job.id))
    session.expire_all()
    paid = session.get(Sale, sale.id)

    assert result == "completed"
    assert paid.payments[0].status is PaymentStatus.SUCCEEDED
    assert paid.payments[0].job.status.value == "completed"
    assert paid.fiscalization is not None
    assert paid.fiscalization_job is not None
    assert paid.status is SaleStatus.FISCALIZATION_PENDING
    assert len(provider.payment_initiations) == 1


def test_payment_worker_quarantines_unknown_command_without_operation_id(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost acquiring response cannot cause a second POST after worker restart."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    provider.payment_error = ProviderCallError(
        "http_500", "AQSI could not return the payment result.", outcome_unknown=True
    )
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, integration_id: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))

    result = payment_jobs_module.run_payment_job(str(sale.payments[0].job.id))
    session.expire_all()
    unknown = session.get(Sale, sale.id)

    assert result == "failed"
    assert unknown.status is SaleStatus.PAYMENT_PENDING
    assert unknown.payments[0].status is PaymentStatus.UNKNOWN
    assert unknown.payments[0].external_id is None
    assert unknown.payments[0].job.status is payment_jobs_module.PaymentJobStatus.FAILED
    assert len(provider.payment_initiations) == 1
    assert payment_jobs_module.run_payment_job(str(unknown.payments[0].job.id)) == "not_claimed"
    assert len(provider.payment_initiations) == 1


def test_payment_worker_with_existing_operation_id_only_polls_status(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A durable provider ID resumes GET reconciliation and never starts a new charge."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    sale.payments[0].external_id = "existing-payment-operation"
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    delayed: list[str] = []
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, integration_id: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(payment_jobs_module, "get_checkout_queue", lambda: object())
    monkeypatch.setattr(
        payment_jobs_module,
        "enqueue_payment_job",
        lambda queue, job_id, delay=0: delayed.append(str(job_id)),
    )

    assert payment_jobs_module.run_payment_job(str(sale.payments[0].job.id)) == "queued"
    assert provider.payment_initiations == []
    assert provider.payment_polls == 1
    assert delayed == [str(sale.payments[0].job.id)]


def test_stale_payment_initiation_without_operation_id_is_not_repeated(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker crash in the cross-system gap quarantines rather than double-charges."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    job = sale.payments[0].job
    job.status = payment_jobs_module.PaymentJobStatus.PROCESSING
    job.last_attempt_at = datetime.now(UTC) - timedelta(minutes=3)
    session.commit()
    session.execute(
        payment_jobs_module.PaymentJob.__table__.update()
        .where(payment_jobs_module.PaymentJob.id == job.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
    )
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(payment_jobs_module, "get_checkout_queue", lambda: object())

    recovered = payment_jobs_module.recover_payment_jobs()
    session.expire_all()
    uncertain = session.get(Sale, sale.id)

    assert recovered == 0
    assert uncertain.payments[0].status is PaymentStatus.UNKNOWN
    assert uncertain.payments[0].job.status.value == "failed"
    assert uncertain.status is SaleStatus.PAYMENT_PENDING


def test_worker_completes_fiscalization_without_browser_request(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The background entry point can fiscalize after the checkout request has ended."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    provider.fiscal_state = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fiscal-operation-1",
        external_reference="receipt-1",
    )
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        fiscal_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, integration_id: binding}
        )(),
    )
    monkeypatch.setattr(fiscal_jobs_module, "FISCALIZATION_POLL_DELAYS", (0.0,))

    result = fiscal_jobs_module.run_fiscalization_job(str(sale.fiscalization_job.id))
    session.expire_all()
    completed = session.get(Sale, sale.id)

    assert result == "completed"
    assert completed.status is SaleStatus.COMPLETED
    assert completed.fiscalization.external_receipt_id == "receipt-1"
    assert completed.fiscalization_job.status.value == "completed"
    assert provider.payment_initiations == []


def test_worker_quarantines_unknown_fiscal_command_without_operation_id(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ambiguous receipt POST is retained and never submitted a second time."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    provider.fiscal_error = ProviderCallError(
        "connect_timeout", "AQSI temporarily unavailable.", outcome_unknown=True
    )
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    delayed: list[tuple[str, int]] = []
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        fiscal_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, integration_id: binding}
        )(),
    )
    monkeypatch.setattr(fiscal_jobs_module, "FISCALIZATION_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(fiscal_jobs_module, "get_checkout_queue", lambda: object())
    monkeypatch.setattr(
        fiscal_jobs_module,
        "enqueue_fiscalization_job",
        lambda queue, job_id, delay=0: delayed.append((str(job_id), delay)),
    )

    result = fiscal_jobs_module.run_fiscalization_job(str(sale.fiscalization_job.id))
    session.expire_all()
    retriable = session.get(Sale, sale.id)

    assert result == "failed"
    assert retriable.status is SaleStatus.FISCALIZATION_PENDING
    assert retriable.fiscalization.status is FiscalizationStatus.UNKNOWN
    assert retriable.fiscalization.external_id is None
    assert retriable.fiscalization_job.status.value == "failed"
    assert retriable.fiscalization_job.attempt_count == 1
    assert delayed == []
    assert len(provider.fiscal_initiations) == 1
    assert (
        fiscal_jobs_module.run_fiscalization_job(str(retriable.fiscalization_job.id))
        == "not_claimed"
    )
    assert len(provider.fiscal_initiations) == 1
    assert provider.payment_initiations == []


def test_restart_recovery_quarantines_stale_fiscal_command_without_operation_id(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash in the receipt command gap cannot cause a second receipt POST."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    job = sale.fiscalization_job
    job.status = fiscal_jobs_module.FiscalizationJobStatus.PROCESSING
    job.last_attempt_at = datetime.now(UTC) - timedelta(minutes=3)
    session.commit()
    session.execute(
        fiscal_jobs_module.FiscalizationJob.__table__.update()
        .where(fiscal_jobs_module.FiscalizationJob.id == job.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
    )
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    queued: list[str] = []
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(fiscal_jobs_module, "get_checkout_queue", lambda: object())
    monkeypatch.setattr(
        fiscal_jobs_module,
        "enqueue_fiscalization_job",
        lambda queue, job_id, delay=0: queued.append(str(job_id)),
    )

    recovered = fiscal_jobs_module.recover_fiscalization_jobs()
    session.expire_all()

    assert recovered == 0
    assert queued == []
    recovered_job = session.get(fiscal_jobs_module.FiscalizationJob, job.id)
    assert recovered_job.status is FiscalizationJobStatus.FAILED
    assert recovered_job.sale.fiscalization.status is FiscalizationStatus.UNKNOWN
    assert recovered_job.sale.status is SaleStatus.FISCALIZATION_PENDING


def test_restart_recovery_requeues_stale_fiscal_status_query_with_operation_id(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale job with a durable provider ID resumes only the safe status query."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(
        sale.id,
        owner_id=user.id,
        payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
    )
    job = sale.fiscalization_job
    sale.fiscalization.external_id = "receipt-operation-1"
    job.aqsi_operation_id = "receipt-operation-1"
    job.status = FiscalizationJobStatus.PROCESSING
    job.last_attempt_at = datetime.now(UTC) - timedelta(minutes=3)
    session.commit()
    session.execute(
        fiscal_jobs_module.FiscalizationJob.__table__.update()
        .where(fiscal_jobs_module.FiscalizationJob.id == job.id)
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=3))
    )
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    queued: list[str] = []
    monkeypatch.setattr(fiscal_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(fiscal_jobs_module, "get_checkout_queue", lambda: object())
    monkeypatch.setattr(
        fiscal_jobs_module,
        "enqueue_fiscalization_job",
        lambda queue, job_id, delay=0: queued.append(str(job_id)),
    )

    assert fiscal_jobs_module.recover_fiscalization_jobs() == 1
    session.expire_all()
    assert queued == [str(job.id)]
    assert (
        session.get(fiscal_jobs_module.FiscalizationJob, job.id).status
        is FiscalizationJobStatus.QUEUED
    )

from __future__ import annotations

import inspect
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import core.main  # noqa: F401 -- register all models required by route-level read checks
import core.sales.checkout as checkout_module
import core.sales.fiscalization_jobs as fiscal_jobs_module
import core.sales.health as sales_health_module
import core.sales.models as sales_models_module
import core.sales.payment_jobs as payment_jobs_module
import core.sales.routes as sales_routes_module
from core.analytics.read_service import SalesAnalyticsReadService, resolve_sales_period
from core.analytics.schemas import SalesPeriodPreset
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.config import Settings
from core.identity.models import User
from core.integrations.aqsi.checkout import AqsiCheckoutAdapter
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.checkout import (
    CheckoutIntegrationUnavailableError,
    CheckoutProviderFactory,
)
from core.integrations.enums import IntegrationProvider, ProviderCapability
from core.integrations.models import Integration
from core.integrations.service import IntegrationConfigurationError, IntegrationService
from core.inventory.enums import MovementType
from core.inventory.models import StockMovement
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.sales.checkout import CheckoutService, CheckoutStateError
from core.sales.enums import (
    CheckoutPaymentOption,
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationJobStatus,
    FiscalizationStatus,
    FiscalVerificationOutcome,
    IncidentResolutionChoice,
    IncidentResolutionStatus,
    IncidentResolutionType,
    PaymentMethod,
    PaymentStatus,
    SaleItemSource,
    SaleStatus,
)
from core.sales.financial_events import append_financial_event, list_financial_events
from core.sales.incident_resolutions import (
    IncidentResolutionConflictError,
    IncidentResolutionNotFoundError,
    IncidentResolutionService,
)
from core.sales.models import (
    FinancialIncidentResolution,
    FinancialOperationEvent,
    PaymentAttempt,
    Sale,
)
from core.sales.providers import (
    CheckoutProviderBinding,
    FiscalLine,
    FiscalRequest,
    PaymentRequest,
    ProviderCallError,
    ProviderErrorKind,
    ProviderHealth,
    ProviderOperation,
    ProviderOutcomeState,
)
from core.sales.repository import SaleRepository
from core.sales.routes import checkout_operation_detail, list_checkout_operations
from core.sales.schemas import (
    CheckoutStartRequest,
    EmergencyFallbackRequest,
    EmergencyIncidentResolutionRequest,
    SaleRead,
)
from core.sales.service import SaleNotDraftError, SaleService
from core.shared.db import Base
from core.shared.db.types import generate_uuid_v7


class FakeCheckoutProvider:
    """Deterministic generic provider used without AQSI/network side effects."""

    def __init__(self) -> None:
        """Initialize pending provider outcomes and call capture lists."""
        self.payment_initiations: list[PaymentRequest] = []
        self.payment_polls = 0
        self.payment_cancellations: list[str] = []
        self.payment_cancellation_error: ProviderCallError | None = None
        self.payment_state_after_cancellation: ProviderOperation | None = None
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

    def request_payment_cancellation(
        self, external_id: str, request: PaymentRequest
    ) -> None:
        """Capture a device cancellation request without simulating a reversal."""
        del request
        self.payment_cancellations.append(external_id)
        if self.payment_cancellation_error:
            raise self.payment_cancellation_error
        if self.payment_state_after_cancellation is not None:
            self.payment_state = self.payment_state_after_cancellation

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


class FakePaymentProvider:
    """Payment-only contract double proving Sales does not require fiscal methods."""

    def __init__(self) -> None:
        """Initialize deterministic payment initiation and polling results."""
        self.initiations: list[PaymentRequest] = []
        self.polls: list[str] = []
        self.cancellations: list[str] = []
        self.initiate_result = ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id="fake-payment-operation",
        )
        self.poll_result = self.initiate_result

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Capture the sole mutating payment command."""
        self.initiations.append(request)
        return self.initiate_result

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Capture a non-mutating recovery lookup by provider operation ID."""
        del request
        self.polls.append(external_id)
        return self.poll_result

    def request_payment_cancellation(
        self, external_id: str, request: PaymentRequest
    ) -> None:
        """Capture one generic device-command cancellation request."""
        del request
        self.cancellations.append(external_id)

    def check_health(self) -> ProviderHealth:
        """Return a stable fake health result."""
        return ProviderHealth(True, datetime.now(UTC).isoformat(), "Fake payment is ready.")


class FakeFiscalProvider:
    """Fiscal-only contract double proving Sales does not require acquiring methods."""

    def __init__(self) -> None:
        """Initialize deterministic receipt initiation and polling results."""
        self.lookups: list[FiscalRequest] = []
        self.initiations: list[FiscalRequest] = []
        self.polls: list[str] = []
        self.initiate_result = ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id="fake-fiscal-operation",
        )
        self.poll_result = self.initiate_result

    def find_fiscalization(self, request: FiscalRequest) -> ProviderOperation | None:
        """Capture the safe preflight lookup."""
        self.lookups.append(request)
        return None

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Capture the sole mutating fiscal command."""
        self.initiations.append(request)
        return self.initiate_result

    def get_fiscalization(
        self,
        external_id: str,
        request: FiscalRequest,
    ) -> ProviderOperation:
        """Capture a non-mutating receipt recovery lookup."""
        del request
        self.polls.append(external_id)
        return self.poll_result

    def check_health(self) -> ProviderHealth:
        """Return a stable fake health result."""
        return ProviderHealth(True, datetime.now(UTC).isoformat(), "Fake fiscal is ready.")


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


def test_independent_fake_provider_contract_recovers_without_duplicate_commands(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Distinct payment/fiscal providers survive polling and complete exactly once locally."""
    user, variant, payment_integration, _, _ = checkout_setup
    fiscal_integration = Integration(
        provider=IntegrationProvider.AQSI,
        name="Independent fiscal provider",
        enabled=True,
        configuration={"payment_enabled": False, "fiscalization_enabled": True},
    )
    session.add(fiscal_integration)
    session.commit()
    payment_provider = FakePaymentProvider()
    fiscal_provider = FakeFiscalProvider()
    binding = CheckoutProviderBinding(
        integration_id=payment_integration.id,
        integration_name=payment_integration.name,
        provider=payment_integration.provider,
        provider_display_name="Fake Pay",
        payment_display_name="Карта / QR",
        payment=payment_provider,
        fiscal=fiscal_provider,
        fiscal_integration_id=fiscal_integration.id,
        fiscal_integration_name=fiscal_integration.name,
        fiscal_provider_kind=fiscal_integration.provider,
        fiscal_provider_display_name="Fake Fiscal",
    )
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)

    started = checkout.start(sale.id, owner_id=user.id)
    assert started.payments[0].selected_fiscal_integration_id == fiscal_integration.id
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    checkout.process_payment(sale.id, owner_id=user.id)

    assert pending.payments[0].external_id == "fake-payment-operation"
    assert len(payment_provider.initiations) == 1
    assert payment_provider.polls == ["fake-payment-operation"]

    payment_provider.poll_result = _succeeded_payment("fake-payment-operation")
    paid = checkout.process_payment(sale.id, owner_id=user.id)

    assert paid.fiscalization is not None
    assert paid.fiscalization.integration_id == fiscal_integration.id
    assert paid.fiscalization.provider == fiscal_integration.provider

    checkout.process_fiscalization(sale.id, owner_id=user.id)
    checkout.process_fiscalization(sale.id, owner_id=user.id)
    fiscal_provider.poll_result = ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_id="fake-fiscal-operation",
        external_reference="fake-receipt",
    )
    completed = checkout.process_fiscalization(sale.id, owner_id=user.id)
    repeated = checkout.process_fiscalization(sale.id, owner_id=user.id)

    assert completed.status is repeated.status is SaleStatus.COMPLETED
    assert len(payment_provider.initiations) == 1
    assert len(fiscal_provider.initiations) == 1
    assert fiscal_provider.polls == ["fake-fiscal-operation", "fake-fiscal-operation"]


def test_provider_contract_rejects_missing_independent_capability(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Missing payment or fiscal capability fails before any financial command is sent."""
    user, variant, integration, _, _ = checkout_setup
    payment_provider = FakePaymentProvider()
    fiscal_provider = FakeFiscalProvider()
    no_payment = CheckoutProviderBinding(
        integration.id,
        integration.name,
        integration.provider,
        "Fake",
        "Карта / QR",
        None,
        fiscal_provider,
    )
    card_sale = _sale_with_item(session, user, variant)

    with pytest.raises(CheckoutStateError, match="Payment provider"):
        CheckoutService(session, no_payment).start(card_sale.id, owner_id=user.id)

    no_fiscal = CheckoutProviderBinding(
        integration.id,
        integration.name,
        integration.provider,
        "Fake",
        "Карта / QR",
        payment_provider,
        None,
    )
    cash_sale = _sale_with_item(session, user, variant)

    with pytest.raises(CheckoutStateError, match="Fiscal provider"):
        CheckoutService(session, no_fiscal).start(
            cash_sale.id,
            owner_id=user.id,
            payment_option=CheckoutPaymentOption.CASH_WITH_RECEIPT,
        )
    assert payment_provider.initiations == []


def test_provider_error_contract_distinguishes_normalized_failure_kinds() -> None:
    """Callers can distinguish ambiguity, safe query retry and unsupported operations."""
    ambiguous = ProviderCallError("timeout", "Unknown result.", outcome_unknown=True)
    definitive = ProviderCallError("declined", "Declined.", outcome_unknown=False)
    temporary = ProviderCallError.temporary_status_query("http_503", "Try status later.")
    unsupported = ProviderCallError.unsupported("refund")

    assert ambiguous.kind is ProviderErrorKind.OUTCOME_UNKNOWN
    assert ambiguous.outcome_unknown is True
    assert definitive.kind is ProviderErrorKind.DEFINITIVE_FAILURE
    assert temporary.kind is ProviderErrorKind.TEMPORARY_STATUS_QUERY_FAILURE
    assert temporary.outcome_unknown is False
    assert unsupported.kind is ProviderErrorKind.UNSUPPORTED_OPERATION


def test_inflight_checkout_keeps_persisted_provider_bindings_after_configuration_change(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical recovery never follows newly selected payment or fiscal Integrations."""
    user, variant, payment_integration, provider, _ = checkout_setup
    fiscal_integration = Integration(
        provider=IntegrationProvider.AQSI,
        name="Original fiscal",
        enabled=True,
        configuration={"payment_enabled": False, "fiscalization_enabled": True},
    )
    session.add(fiscal_integration)
    session.commit()
    binding = CheckoutProviderBinding(
        integration_id=payment_integration.id,
        integration_name=payment_integration.name,
        provider=payment_integration.provider,
        provider_display_name="Original payment",
        payment_display_name="Карта / QR",
        payment=provider,
        fiscal=provider,
        fiscal_integration_id=fiscal_integration.id,
        fiscal_integration_name=fiscal_integration.name,
        fiscal_provider_kind=fiscal_integration.provider,
        fiscal_provider_display_name="Original fiscal",
    )
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    payment_integration.enabled = False
    fiscal_integration.enabled = False
    replacement = Integration(
        provider=IntegrationProvider.AQSI,
        name="New default",
        enabled=True,
        configuration={},
    )
    session.add(replacement)
    session.commit()
    captured: list[tuple[object, object]] = []

    class RecordingFactory:
        def __init__(self, database_session: Session, settings: Settings) -> None:
            del database_session, settings

        def for_recovery(self, **kwargs: object) -> CheckoutProviderBinding:
            captured.append(
                (kwargs["payment_integration_id"], kwargs["fiscal_integration_id"])
            )
            return binding

        def for_new_checkout(self) -> CheckoutProviderBinding:
            raise AssertionError("In-flight checkout must not resolve new defaults.")

    monkeypatch.setattr(sales_routes_module, "CheckoutProviderFactory", RecordingFactory)

    resolved = sales_routes_module._checkout_binding(
        session,
        Settings(jwt_secret="test-only-jwt-secret-at-least-32-bytes"),
        sale,
    )

    assert resolved is binding
    assert captured == [(payment_integration.id, fiscal_integration.id)]
    assert replacement.id not in captured[0]


def test_restarted_worker_uses_persisted_provider_bindings_after_configuration_change(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker restart cannot redirect an in-flight command to new default providers."""
    user, variant, payment_integration, provider, _ = checkout_setup
    fiscal_integration = Integration(
        provider=IntegrationProvider.AQSI,
        name="Original worker fiscal",
        enabled=True,
        configuration={"payment_enabled": False, "fiscalization_enabled": True},
    )
    session.add(fiscal_integration)
    session.commit()
    binding = CheckoutProviderBinding(
        integration_id=payment_integration.id,
        integration_name=payment_integration.name,
        provider=payment_integration.provider,
        provider_display_name="Original payment",
        payment_display_name="Карта / QR",
        payment=provider,
        fiscal=provider,
        fiscal_integration_id=fiscal_integration.id,
        fiscal_integration_name=fiscal_integration.name,
        fiscal_provider_kind=fiscal_integration.provider,
        fiscal_provider_display_name="Original fiscal",
    )
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    payment_integration.enabled = False
    fiscal_integration.enabled = False
    session.add(
        Integration(
            provider=IntegrationProvider.AQSI,
            name="Replacement after restart",
            enabled=True,
            configuration={},
        )
    )
    session.commit()
    provider.payment_state = _succeeded_payment()
    captured: list[tuple[object, object]] = []

    class RecordingFactory:
        def __init__(self, database_session: Session, settings: Settings) -> None:
            del database_session, settings

        def for_recovery(self, **kwargs: object) -> CheckoutProviderBinding:
            captured.append(
                (kwargs["payment_integration_id"], kwargs["fiscal_integration_id"])
            )
            return binding

    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(payment_jobs_module, "CheckoutProviderFactory", RecordingFactory)
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(payment_jobs_module, "enqueue_sale_fiscalization", lambda sale_id: True)

    result = payment_jobs_module.run_payment_job(str(sale.payments[0].job.id))

    assert result == "completed"
    assert captured == [(payment_integration.id, fiscal_integration.id)]
    assert len(provider.payment_initiations) == 1


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
    list_settings = Settings(jwt_secret="test-only-jwt-secret-at-least-32-bytes")
    assert sale in list_checkout_operations(
        session,
        list_settings,
        user,
        state="attention",
        date_from=None,
        date_to=None,
        limit=100,
    )
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
        session, list_settings, user, state="all", date_from=None, date_to=None, limit=100
    )
    assert dismissed not in list_checkout_operations(
        session, list_settings, user, state="failed", date_from=None, date_to=None, limit=100
    )
    assert dismissed not in list_checkout_operations(
        session,
        list_settings,
        user,
        state="attention",
        date_from=None,
        date_to=None,
        limit=100,
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


def test_checkout_factory_selects_payment_and_fiscal_integrations_independently(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Two boolean assignments split capabilities while legacy defaults still select both."""
    _, _, payment_integration, _, _ = checkout_setup
    factory = CheckoutProviderFactory(
        session,
        Settings(jwt_secret="test-only-jwt-secret-at-least-32-bytes"),
    )

    assert factory._select(ProviderCapability.PAYMENT).id == payment_integration.id
    assert factory._select(ProviderCapability.FISCALIZATION).id == payment_integration.id

    payment_integration.configuration = {
        "payment_enabled": True,
        "fiscalization_enabled": False,
    }
    fiscal_integration = Integration(
        provider=IntegrationProvider.AQSI,
        name="Fiscal only",
        enabled=True,
        configuration={"payment_enabled": False, "fiscalization_enabled": True},
    )
    session.add(fiscal_integration)
    session.commit()

    assert factory._select(ProviderCapability.PAYMENT).id == payment_integration.id
    assert factory._select(ProviderCapability.FISCALIZATION).id == fiscal_integration.id


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
        self.cancellation_calls: list[str] = []

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Capture acquiring payload."""
        self.purchase_payload = payload
        return {"operationId": "payment-op"}

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Return configured operation state."""
        self.operation_calls.append(operation_id)
        return self.operation

    def cancel_operation(self, operation_id: str) -> dict[str, object]:
        """Capture one device-operation cancellation command."""
        self.cancellation_calls.append(operation_id)
        return {"operationId": operation_id}

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


def test_aqsi_adapter_requests_device_cancellation_without_claiming_final_state() -> None:
    """AQSI cancellation is a command only; final state still comes from GET."""
    settings = Settings(
        jwt_secret="test-only-jwt-secret-at-least-32-bytes",
        aqsi_api_key=SecretStr("not-used-by-double"),
        aqsi_sale_spike_device_id="748887",
        aqsi_sale_spike_tax_system_code=2,
        aqsi_tax_code=6,
    )
    client = RecordingAqsiClient()
    adapter = AqsiCheckoutAdapter(settings, cast(AqsiHttpClient, client))
    request = PaymentRequest(
        sale_id=generate_uuid_v7(),
        attempt_id=generate_uuid_v7(),
        idempotency_key="payment-key",
        amount=Decimal("10.00"),
        currency="RUB",
        method=PaymentMethod.CARD,
    )

    result = adapter.request_payment_cancellation("payment-op", request)

    assert result is None
    assert client.cancellation_calls == ["payment-op"]
    assert client.operation_calls == []


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
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
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
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
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
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
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
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
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
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
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


def test_financial_journal_records_meaningful_transitions_without_poll_flood(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Creation and provider identity are journaled once; repeated pending GETs are quiet."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)

    checkout.start(sale.id, owner_id=user.id)
    checkout.process_payment(sale.id, owner_id=user.id)
    count_after_identity = session.scalar(
        select(func.count(FinancialOperationEvent.id)).where(
            FinancialOperationEvent.sale_id == sale.id
        )
    )
    checkout.process_payment(sale.id, owner_id=user.id)
    events = list_financial_events(session, sale_id=sale.id)

    assert provider.payment_polls == 1
    assert len(events) == count_after_identity
    assert [event.event_type for event in reversed(events)] == [
        FinancialEventType.PAYMENT_CREATED,
        FinancialEventType.PAYMENT_COMMAND_PREPARED,
        FinancialEventType.PAYMENT_OPERATION_ID_RECEIVED,
    ]


def test_financial_state_and_event_rollback_together(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A rolled-back domain transition cannot leave orphan audit evidence."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    attempt = sale.payments[0]
    baseline = session.scalar(select(func.count(FinancialOperationEvent.id)))

    attempt.status = PaymentStatus.UNKNOWN
    append_financial_event(
        session,
        sale_id=sale.id,
        entity_type=FinancialEntityType.PAYMENT_ATTEMPT,
        entity_id=attempt.id,
        event_type=FinancialEventType.PAYMENT_OUTCOME_UNKNOWN,
        correlation_id=attempt.job.correlation_id,
        actor_type=FinancialActorType.SYSTEM,
        previous_status=PaymentStatus.PENDING.value,
        new_status=PaymentStatus.UNKNOWN.value,
    )
    session.flush()
    session.rollback()
    session.expire_all()

    assert session.get(PaymentAttempt, attempt.id).status is PaymentStatus.PENDING
    assert session.scalar(select(func.count(FinancialOperationEvent.id))) == baseline


def test_financial_events_are_immutable_through_orm(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Normal ORM writes cannot rewrite append-only financial evidence."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    event = session.scalar(
        select(FinancialOperationEvent).where(FinancialOperationEvent.sale_id == sale.id)
    )

    event.safe_message = "rewritten"
    with pytest.raises(RuntimeError, match="immutable"):
        session.commit()
    session.rollback()


def test_operation_detail_enforces_sale_ownership(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """The journal is never exposed to an unrelated authenticated operator."""
    owner, variant, _, _, binding = checkout_setup
    outsider = User(email="outsider@example.com", full_name="Outsider", password_hash="unused")
    session.add(outsider)
    session.commit()
    sale = _sale_with_item(session, owner, variant)
    CheckoutService(session, binding).start(sale.id, owner_id=owner.id)

    with pytest.raises(HTTPException) as error:
        checkout_operation_detail(sale.id, session, outsider, 100, None)

    assert error.value.status_code == 404


def test_operation_detail_identifies_older_unknown_attempt_for_manual_review(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A later bookkeeping payment cannot hide an older unresolved provider attempt."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    unresolved = sale.payments[0]
    unresolved.status = PaymentStatus.UNKNOWN
    unresolved.provider_metadata = {"accounting_superseded": True}
    sale.status = SaleStatus.COMPLETED
    replacement = PaymentAttempt(
        sale_id=sale.id,
        payment_method=PaymentMethod.CASH,
        requested_amount=sale.total_amount,
        fiscalization_required=False,
        status=PaymentStatus.SUCCEEDED,
        idempotency_key=f"sale:{sale.id}:payment:2",
        attempt_number=2,
    )
    session.add(replacement)
    session.commit()

    detail = checkout_operation_detail(sale.id, session, user, 100, None)

    assert detail.manual_review_required is True
    assert detail.manual_review_payment_id == unresolved.id


def test_missing_provider_id_reconciliation_is_idempotent_manual_review(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Repeated checks without a provider ID create no command and one review marker pair."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    attempt = sale.payments[0]
    attempt.status = PaymentStatus.UNKNOWN
    attempt.job.status = payment_jobs_module.PaymentJobStatus.FAILED
    session.commit()

    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            sales_routes_module.reconcile_checkout_operation(sale.id, session, user)
        assert error.value.status_code == 409

    review_events = session.scalars(
        select(FinancialOperationEvent).where(
            FinancialOperationEvent.correlation_id == attempt.job.correlation_id,
            FinancialOperationEvent.event_type.in_(
                {
                    FinancialEventType.RECONCILIATION_INCONCLUSIVE,
                    FinancialEventType.MANUAL_REVIEW_REQUIRED,
                }
            ),
        )
    ).all()
    assert len(review_events) == 2
    assert provider.payment_initiations == []
    assert (
        session.scalar(
            select(func.count(PaymentAttempt.id)).where(PaymentAttempt.sale_id == sale.id)
        )
        == 1
    )
def test_known_id_reconciliation_reuses_payment_and_inventory_once(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator check queues authoritative GET and completes through normal business logic."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    attempt = sale.payments[0]
    attempt.external_id = "known-payment-operation"
    attempt.status = PaymentStatus.UNKNOWN
    attempt.job.status = payment_jobs_module.PaymentJobStatus.FAILED
    attempt.job.attempt_count = payment_jobs_module.MAX_UNKNOWN_QUERY_RUNS
    session.commit()
    wakeups: list[object] = []
    monkeypatch.setattr(
        sales_routes_module, "enqueue_sale_payment", lambda sale_id: wakeups.append(sale_id)
    )

    sales_routes_module.reconcile_checkout_operation(sale.id, session, user)
    sales_routes_module.reconcile_checkout_operation(sale.id, session, user)

    assert wakeups == [sale.id]
    assert provider.payment_initiations == []
    assert session.scalar(
        select(func.count(FinancialOperationEvent.id)).where(
            FinancialOperationEvent.correlation_id == attempt.job.correlation_id,
            FinancialOperationEvent.event_type == FinancialEventType.RECONCILIATION_REQUESTED,
        )
    ) == 1
    provider.payment_state = _succeeded_payment("known-payment-operation")
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(payment_jobs_module, "enqueue_sale_fiscalization", lambda sale_id: True)

    assert payment_jobs_module.run_payment_job(str(attempt.job.id)) == "completed"
    session.expire_all()
    completed = session.get(Sale, sale.id)

    assert completed.payments[0].status is PaymentStatus.SUCCEEDED
    assert provider.payment_initiations == []
    assert provider.payment_polls == 1
    assert session.query(StockMovement).count() == 1
    assert completed.fiscalization_job is not None
    with pytest.raises(HTTPException):
        sales_routes_module.reconcile_checkout_operation(sale.id, session, user)
    assert session.query(StockMovement).count() == 1


def test_emergency_fallback_preserves_unknown_attempt_and_completes_once(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """External POS confirmation reuses Sale accounting without another provider command."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    checkout.process_payment(sale.id, owner_id=user.id)
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.UNKNOWN,
        external_id="payment-operation-1",
        error_code="provider_pending",
    )
    unknown = checkout.process_payment(sale.id, owner_id=user.id)
    original = unknown.payments[0]

    completed = checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    repeated = checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )

    assert completed.status is repeated.status is SaleStatus.COMPLETED
    assert len(completed.payments) == 2
    assert original.status is PaymentStatus.UNKNOWN
    assert original.external_id == "payment-operation-1"
    assert original.accounting_superseded is True
    fallback = completed.payments[1]
    assert fallback.status is PaymentStatus.SUCCEEDED
    assert fallback.emergency_fallback is True
    assert fallback.provider_metadata["source"] == "EXTERNAL_POS"
    assert fallback.provider_metadata["confirmation"] == "OPERATOR_CONFIRMED"
    assert fallback.requested_amount == completed.total_amount
    assert fallback.linked_payment_attempt_id == str(original.id)
    assert completed.fiscalization.status is FiscalizationStatus.SKIPPED
    assert completed.fiscalization.external_receipt_confirmed is True
    assert completed.fiscalization_job is None
    assert session.query(StockMovement).count() == 1
    assert len(provider.payment_initiations) == 1
    assert provider.fiscal_initiations == []
    assert (
        session.scalar(
            select(func.count(Sale.id)).where(
                Sale.id == sale.id,
                Sale.status == SaleStatus.COMPLETED,
            )
        )
        == 1
    )
    period = resolve_sales_period(
        SalesPeriodPreset.TODAY,
        "UTC",
        now=completed.completed_at,
    )
    analytics = SalesAnalyticsReadService(session, "UTC")
    summary = analytics.summary(period)
    methods = analytics.payment_methods(period)
    assert summary.sale_count == 1
    assert summary.sales_revenue == completed.total_amount
    assert [(item.payment_method, item.revenue) for item in methods] == [
        (PaymentMethod.CASH.value, completed.total_amount)
    ]
    event_types = {
        event.event_type for event in list_financial_events(session, sale_id=sale.id)
    }
    assert {
        FinancialEventType.EMERGENCY_FALLBACK_REQUESTED,
        FinancialEventType.EMERGENCY_FALLBACK_CONFIRMED,
        FinancialEventType.EXTERNAL_PAYMENT_RECORDED,
        FinancialEventType.MANUAL_REVIEW_REQUIRED,
    }.issubset(event_types)


def test_emergency_fallback_without_provider_id_retains_manual_review(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A missing AQSI operation ID never becomes a provider failure or a new charge."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    original = sale.payments[0]
    original.status = PaymentStatus.UNKNOWN
    original.job.status = payment_jobs_module.PaymentJobStatus.FAILED
    session.commit()

    completed = CheckoutService(session, binding).confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )

    assert completed.status is SaleStatus.COMPLETED
    assert original.status is PaymentStatus.UNKNOWN
    assert original.external_id is None
    assert original.job.status is payment_jobs_module.PaymentJobStatus.FAILED
    assert provider.payment_initiations == []
    detail = checkout_operation_detail(sale.id, session, user, 100, None)
    assert detail.manual_review_required is True
    assert detail.emergency_fallback_recorded is True
    assert detail.manual_review_payment_id == original.id


def test_payment_approval_wins_race_and_rejects_emergency_fallback(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """A durably applied provider approval blocks a second effective payment fact."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    provider.payment_state = _succeeded_payment()
    approved = checkout.process_payment(sale.id, owner_id=user.id)

    with pytest.raises(CheckoutStateError, match="уже подтверждена"):
        checkout.confirm_external_pos_payment(
            sale.id,
            owner_id=user.id,
            risk_acknowledged=True,
        )

    assert approved.status is SaleStatus.FISCALIZATION_PENDING
    assert len(approved.payments) == 1
    assert session.query(StockMovement).count() == 1


def test_fallback_wins_race_and_late_approval_creates_duplicate_risk(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Late AQSI approval is retained but cannot repost stock, revenue or fiscalization."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(original.external_id)

    completed = checkout.process_payment(
        sale.id,
        owner_id=user.id,
        attempt_id=original.id,
    )

    assert completed.status is SaleStatus.COMPLETED
    assert original.status is PaymentStatus.SUCCEEDED
    assert original.duplicate_payment_risk is True
    assert original.accounting_superseded is True
    assert len([item for item in completed.payments if not item.accounting_superseded]) == 1
    assert session.query(StockMovement).count() == 1
    assert completed.fiscalization.status is FiscalizationStatus.SKIPPED
    assert completed.fiscalization_job is None
    assert provider.fiscal_initiations == []
    detail = checkout_operation_detail(sale.id, session, user, 100, None)
    assert detail.duplicate_payment_risk is True
    assert detail.recovery_state == "duplicate_payment_risk"
    assert FinancialEventType.LATE_PROVIDER_APPROVAL_DETECTED in {
        event.event_type for event in detail.events
    }


def test_operator_resolves_single_charge_without_new_financial_operation(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Resolution preserves both facts while inventory and revenue remain exactly once."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(cast(str, original.external_id))
    completed = checkout.process_payment(
        sale.id,
        owner_id=user.id,
        attempt_id=original.id,
    )
    payment_count = len(completed.payments)
    stock_count = session.query(StockMovement).count()
    fiscal_count = len(provider.fiscal_initiations)
    initiation_count = len(provider.payment_initiations)

    service = IncidentResolutionService(session)
    resolution = service.confirm_single_charge(
        sale.id,
        owner_id=user.id,
        confirmation=True,
        evidence_reference=cast(str, original.external_id),
        operator_note="Сверена одна операция 310 ₽ в личном кабинете AQSI.",
    )
    repeated = service.confirm_single_charge(
        sale.id,
        owner_id=user.id,
        confirmation=True,
        evidence_reference="повторная отправка формы",
    )

    assert repeated.id == resolution.id
    assert resolution.resolution_type is IncidentResolutionType.SINGLE_CHARGE_OPERATOR_CONFIRMED
    assert resolution.resolution_status is IncidentResolutionStatus.RESOLVED
    assert len(completed.payments) == payment_count == 2
    assert original.status is PaymentStatus.SUCCEEDED
    assert original.duplicate_payment_risk is True
    assert original.accounting_superseded is True
    assert session.query(StockMovement).count() == stock_count == 1
    assert len(provider.payment_initiations) == initiation_count == 1
    assert len(provider.fiscal_initiations) == fiscal_count == 0
    assert session.query(FinancialIncidentResolution).count() == 1
    for event_type in (
        FinancialEventType.PAYMENT_INCIDENT_REVIEWED,
        FinancialEventType.SINGLE_CHARGE_OPERATOR_CONFIRMED,
        FinancialEventType.PAYMENT_INCIDENT_RESOLVED,
    ):
        assert (
            session.scalar(
                select(func.count(FinancialOperationEvent.id)).where(
                    FinancialOperationEvent.sale_id == sale.id,
                    FinancialOperationEvent.event_type == event_type,
                )
            )
            == 1
        )
    detail = checkout_operation_detail(sale.id, session, user, 100, None)
    assert detail.duplicate_payment_risk is True
    assert detail.payment_incident_unresolved is False
    assert detail.payment_resolution.id == resolution.id
    assert detail.fiscal_verification_state == "operator_reported"
    assert detail.manual_review_required is True
    assert detail.recovery_state == "fiscal_verification_required"

    period = resolve_sales_period(
        SalesPeriodPreset.TODAY,
        "UTC",
        now=completed.completed_at,
    )
    analytics = SalesAnalyticsReadService(session, "UTC")
    assert analytics.summary(period).sales_revenue == completed.total_amount
    assert sum(item.revenue for item in analytics.payment_methods(period)) == completed.total_amount


def test_fiscal_review_missing_unknown_and_later_verified_are_separate_from_payment(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Fiscal evidence can progress to verified without payment or fiscal provider commands."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(cast(str, original.external_id))
    checkout.process_payment(sale.id, owner_id=user.id, attempt_id=original.id)
    resolutions = IncidentResolutionService(session)
    resolutions.confirm_single_charge(
        sale.id,
        owner_id=user.id,
        confirmation=True,
        evidence_reference=cast(str, original.external_id),
    )
    initial_payment_calls = len(provider.payment_initiations)

    unknown = resolutions.record_fiscal_verification(
        sale.id,
        owner_id=user.id,
        outcome=FiscalVerificationOutcome.UNKNOWN,
        confirmation=True,
        operator_note="В архиве смены чек пока не найден.",
    )
    assert unknown.resolution_status is IncidentResolutionStatus.UNKNOWN
    missing = resolutions.record_fiscal_verification(
        sale.id,
        owner_id=user.id,
        outcome=FiscalVerificationOutcome.MISSING,
        confirmation=True,
        operator_note="На кассе найден только эквайринговый приход.",
    )
    assert missing.id == unknown.id
    assert missing.resolution_status is IncidentResolutionStatus.REQUIRES_ACTION
    missing_detail = checkout_operation_detail(sale.id, session, user, 100, None)
    assert missing_detail.payment_incident_unresolved is False
    assert missing_detail.fiscal_verification_state == "missing"
    assert missing_detail.manual_review_required is True
    assert missing_detail.recovery_state == "fiscal_receipt_missing"
    settings = Settings(jwt_secret="test", _env_file=None)
    assert sale in list_checkout_operations(
        session,
        settings,
        user,
        state="attention",
        date_from=None,
        date_to=None,
        limit=100,
    )

    verified = resolutions.record_fiscal_verification(
        sale.id,
        owner_id=user.id,
        outcome=FiscalVerificationOutcome.VERIFIED,
        confirmation=True,
        evidence_reference="Сверка архива смены AQSI",
        operator_note="Фискальный документ найден после повторной сверки.",
        fiscal_fn="9999078900001234",
        fiscal_fd="1678",
        fiscal_fpd="1234567890",
    )
    repeated = resolutions.record_fiscal_verification(
        sale.id,
        owner_id=user.id,
        outcome=FiscalVerificationOutcome.VERIFIED,
        confirmation=True,
        evidence_reference="повторная отправка",
    )

    assert repeated.id == verified.id == unknown.id
    assert verified.resolution_status is IncidentResolutionStatus.RESOLVED
    assert verified.fiscal_fn == "9999078900001234"
    assert verified.fiscal_fd == "1678"
    assert verified.fiscal_fpd == "1234567890"
    assert sale.fiscalization.status is FiscalizationStatus.SKIPPED
    assert sale.fiscalization.external_receipt_confirmed is True
    assert len(provider.payment_initiations) == initial_payment_calls
    assert provider.fiscal_initiations == []
    assert session.query(StockMovement).count() == 1
    detail = checkout_operation_detail(sale.id, session, user, 100, None)
    assert detail.fiscal_verification_state == "verified"
    assert detail.manual_review_required is False
    assert detail.recovery_state == "external_pos_completed"
    assert sale not in list_checkout_operations(
        session,
        settings,
        user,
        state="attention",
        date_from=None,
        date_to=None,
        limit=100,
    )
    assert {
        FinancialEventType.FISCAL_VERIFICATION_UNKNOWN_RECORDED,
        FinancialEventType.FISCAL_RECEIPT_MISSING_REPORTED,
        FinancialEventType.FISCAL_RECEIPT_VERIFIED,
    }.issubset({event.event_type for event in detail.events})


def test_one_incident_resolution_flow_keeps_payment_and_fiscal_facts_separate(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compact choices reuse current facts and never clear another unresolved track."""
    user, variant, _, provider, binding = checkout_setup
    user.is_admin = True
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    monkeypatch.setattr(sales_routes_module, "enqueue_sale_payment", lambda sale_id: True)

    payment_result = sales_routes_module.resolve_emergency_incident(
        sale.id,
        EmergencyIncidentResolutionRequest(
            choice=IncidentResolutionChoice.EXTERNAL_POS_PAYMENT_CONFIRMED,
            confirmation=True,
            operator_note="Оплата и чек проверены на физической кассе.",
        ),
        session,
        user,
    )
    payment_detail = checkout_operation_detail(sale.id, session, user, 100, None)

    assert payment_result.choice is IncidentResolutionChoice.EXTERNAL_POS_PAYMENT_CONFIRMED
    assert payment_detail.payment_resolution is not None
    assert (
        payment_detail.payment_resolution.resolution_type
        is IncidentResolutionType.EXTERNAL_POS_PAYMENT_CONFIRMED
    )
    assert (
        payment_detail.payment_resolution.resolution_status
        is IncidentResolutionStatus.REQUIRES_ACTION
    )
    assert payment_detail.fiscal_verification_state == "operator_reported"
    assert payment_detail.fiscal_action_required is True
    assert payment_detail.manual_review_required is True
    assert len(provider.payment_initiations) == 1
    assert provider.fiscal_initiations == []
    assert session.query(StockMovement).count() == 1

    original.status = PaymentStatus.CANCELED
    session.commit()
    canceled_result = sales_routes_module.resolve_emergency_incident(
        sale.id,
        EmergencyIncidentResolutionRequest(
            choice=IncidentResolutionChoice.ORIGINAL_OPERATION_CANCELED,
            confirmation=True,
        ),
        session,
        user,
    )
    canceled_detail = checkout_operation_detail(sale.id, session, user, 100, None)

    assert canceled_result.choice is IncidentResolutionChoice.ORIGINAL_OPERATION_CANCELED
    assert (
        canceled_detail.payment_resolution.resolution_type
        is IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED
    )
    assert (
        canceled_detail.payment_resolution.resolution_status
        is IncidentResolutionStatus.RESOLVED
    )
    assert canceled_detail.fiscal_action_required is True
    assert canceled_detail.manual_review_required is True

    correction_result = sales_routes_module.resolve_emergency_incident(
        sale.id,
        EmergencyIncidentResolutionRequest(
            choice=IncidentResolutionChoice.CORRECTION_RECEIPT_FORMED,
            confirmation=True,
            operator_note="Чек коррекции сверён в архиве смены.",
        ),
        session,
        user,
    )
    final_detail = checkout_operation_detail(sale.id, session, user, 100, None)

    assert correction_result.choice is IncidentResolutionChoice.CORRECTION_RECEIPT_FORMED
    assert final_detail.fiscal_verification_state == "verified"
    assert final_detail.manual_review_required is False
    assert original.external_id is not None
    assert len(provider.payment_initiations) == 1
    assert provider.fiscal_initiations == []
    assert session.query(StockMovement).count() == 1


def test_manual_resolution_choice_retains_unknown_payment_and_event_history(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Manual review is evidence, not a fabricated provider conclusion."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    original.status = PaymentStatus.UNKNOWN
    session.commit()
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    event_count = session.query(FinancialOperationEvent).count()

    resolution = IncidentResolutionService(session).record_payment_review(
        sale.id,
        owner_id=user.id,
        resolution_type=IncidentResolutionType.MANUAL_REVIEW_CONFIRMED,
        confirmation=True,
        operator_note="Сверено вручную, результат банка не установлен.",
    )

    assert resolution.resolution_status is IncidentResolutionStatus.UNKNOWN
    assert original.status is PaymentStatus.UNKNOWN
    assert original.external_id is not None
    assert session.query(FinancialOperationEvent).count() == event_count + 1
    assert session.query(StockMovement).count() == 1
    assert sale.fiscalization.status is FiscalizationStatus.SKIPPED


def test_operation_detail_uses_frozen_sale_item_snapshots(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Operational detail never substitutes current Catalog names or prices."""
    user, variant, _, _, _ = checkout_setup
    sale = _sale_with_item(session, user, variant, quantity=2)
    frozen = sale.items[0]
    frozen_name = frozen.display_label_snapshot
    frozen_price = frozen.unit_price
    variant.title = "Новое имя после продажи"
    price = session.scalar(select(Price).where(Price.variant_id == variant.id))
    price.amount = Decimal("999.00")
    session.commit()

    detail = checkout_operation_detail(sale.id, session, user, 100, None)
    item = detail.sale.items[0]

    assert item.display_label_snapshot == frozen_name
    assert item.unit_price == frozen_price
    assert item.quantity == 2
    assert item.line_total == frozen_price * 2


def test_incident_resolution_rejects_unowned_and_unconfirmed_requests(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Ownership and explicit attestation are enforced before any audit mutation."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(cast(str, original.external_id))
    checkout.process_payment(sale.id, owner_id=user.id, attempt_id=original.id)
    other = User(email="other@example.com", full_name="Other", password_hash="unused")
    session.add(other)
    session.commit()
    service = IncidentResolutionService(session)

    with pytest.raises(IncidentResolutionNotFoundError):
        service.confirm_single_charge(
            sale.id,
            owner_id=other.id,
            confirmation=True,
            evidence_reference=cast(str, original.external_id),
        )
    with pytest.raises(IncidentResolutionConflictError, match="Подтвердите"):
        service.confirm_single_charge(
            sale.id,
            owner_id=user.id,
            confirmation=False,
            evidence_reference=cast(str, original.external_id),
        )
    assert session.query(FinancialIncidentResolution).count() == 0


def test_single_charge_resolution_is_blocked_when_two_provider_charges_are_known(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
) -> None:
    """Known multiple provider approvals remain an unresolved refund incident."""
    user, variant, integration, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(cast(str, original.external_id))
    checkout.process_payment(sale.id, owner_id=user.id, attempt_id=original.id)
    second_charge = PaymentAttempt(
        sale_id=sale.id,
        integration_id=integration.id,
        provider=IntegrationProvider.AQSI,
        payment_method=PaymentMethod.CARD,
        requested_amount=sale.total_amount,
        fiscalization_required=True,
        currency="RUB",
        external_id="known-second-provider-charge",
        status=PaymentStatus.SUCCEEDED,
        idempotency_key=f"sale:{sale.id}:known-second-charge",
        attempt_number=3,
        provider_metadata={},
        created_by_id=user.id,
        updated_by_id=user.id,
    )
    sale.payments.append(second_charge)
    session.commit()

    with pytest.raises(IncidentResolutionConflictError, match="несколько"):
        IncidentResolutionService(session).confirm_single_charge(
            sale.id,
            owner_id=user.id,
            confirmation=True,
            evidence_reference=cast(str, original.external_id),
        )

    assert session.query(FinancialIncidentResolution).count() == 0
    assert session.query(StockMovement).count() == 1
    assert provider.fiscal_initiations == []


def test_emergency_fallback_endpoint_requires_admin_and_reconciliation_remains_read_only(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an administrator can override accounting, while later checks only poll by ID."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    checkout.process_payment(sale.id, owner_id=user.id)
    request = EmergencyFallbackRequest(risk_acknowledged=True)

    with pytest.raises(HTTPException) as forbidden:
        sales_routes_module.confirm_external_pos_payment(sale.id, request, session, user)
    assert forbidden.value.status_code == 403

    user.is_admin = True
    session.commit()
    wakeups: list[object] = []
    monkeypatch.setattr(
        sales_routes_module, "enqueue_sale_payment", lambda sale_id: wakeups.append(sale_id)
    )
    completed = sales_routes_module.confirm_external_pos_payment(sale.id, request, session, user)
    sales_routes_module.reconcile_checkout_operation(sale.id, session, user)

    assert completed.status is SaleStatus.COMPLETED
    assert wakeups == [sale.id]
    assert len(provider.payment_initiations) == 1
    assert session.query(StockMovement).count() == 1


def test_payment_worker_records_late_approval_against_original_attempt(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restarted worker targets its own attempt and never the newer fallback fact."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = _succeeded_payment(original.external_id)
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0,))
    monkeypatch.setattr(payment_jobs_module, "enqueue_sale_fiscalization", lambda sale_id: False)

    assert payment_jobs_module.run_payment_job(str(original.job.id)) == "completed"
    session.expire_all()
    completed = session.get(Sale, sale.id)
    original = next(item for item in completed.payments if not item.emergency_fallback)

    assert completed.status is SaleStatus.COMPLETED
    assert original.status is PaymentStatus.SUCCEEDED
    assert original.duplicate_payment_risk is True
    assert provider.payment_polls == 1
    assert len(provider.payment_initiations) == 1
    assert session.query(StockMovement).count() == 1
    assert completed.fiscalization_job is None
    assert provider.payment_cancellations == []


def test_emergency_worker_cancels_device_command_then_verifies_final_status(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker polls first, requests one cancellation, then trusts only final GET state."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING,
        external_id=cast(str, original.external_id),
    )
    provider.payment_state_after_cancellation = ProviderOperation(
        ProviderOutcomeState.CANCELED,
        external_id=cast(str, original.external_id),
    )
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(payment_jobs_module, "enqueue_sale_fiscalization", lambda sale_id: False)

    assert payment_jobs_module.run_payment_job(str(original.job.id)) == "completed"
    session.expire_all()
    completed = session.get(Sale, sale.id)
    original = next(item for item in completed.payments if not item.emergency_fallback)

    assert provider.payment_polls == 2
    assert provider.payment_cancellations == [original.external_id]
    assert original.status is PaymentStatus.CANCELED
    assert original.provider_metadata["emergency_cancellation"]["state"] == "requested"
    assert completed.status is SaleStatus.COMPLETED
    assert len([item for item in completed.payments if not item.accounting_superseded]) == 1
    assert session.query(StockMovement).count() == 1
    assert completed.fiscalization.status is FiscalizationStatus.SKIPPED
    assert provider.fiscal_initiations == []


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_emergency_worker_does_not_repeat_failed_or_ambiguous_cancellation_post(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
    outcome_unknown: bool,
) -> None:
    """A failed cancellation response causes GET retries, never a blind second POST."""
    user, variant, _, provider, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    checkout = CheckoutService(session, binding)
    checkout.start(sale.id, owner_id=user.id)
    pending = checkout.process_payment(sale.id, owner_id=user.id)
    original = pending.payments[0]
    checkout.confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    provider.payment_state = ProviderOperation(
        ProviderOutcomeState.PENDING,
        external_id=cast(str, original.external_id),
    )
    provider.payment_cancellation_error = ProviderCallError(
        "timeout",
        "Cancellation response was not received.",
        outcome_unknown=outcome_unknown,
    )
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(
        payment_jobs_module,
        "CheckoutProviderFactory",
        lambda database_session, settings: type(
            "Factory", (), {"for_recovery": lambda self, **kwargs: binding}
        )(),
    )
    monkeypatch.setattr(payment_jobs_module, "PAYMENT_POLL_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(payment_jobs_module, "get_checkout_queue", lambda: object())
    monkeypatch.setattr(payment_jobs_module, "enqueue_payment_job", lambda *args, **kwargs: None)

    assert payment_jobs_module.run_payment_job(str(original.job.id)) == "queued"
    session.expire_all()
    original = next(
        item for item in session.get(Sale, sale.id).payments if not item.emergency_fallback
    )
    original.job.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert payment_jobs_module.run_payment_job(str(original.job.id)) == "queued"
    session.expire_all()
    original = next(
        item for item in session.get(Sale, sale.id).payments if not item.emergency_fallback
    )

    assert provider.payment_cancellations == [original.external_id]
    assert provider.payment_polls >= 4
    assert original.status is PaymentStatus.PENDING
    assert original.provider_metadata["emergency_cancellation"]["state"] == "unknown"
    assert session.query(StockMovement).count() == 1
    assert session.get(Sale, sale.id).fiscalization_job is None


def test_payment_recovery_never_reopens_completed_external_pos_sale(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery quarantines a command without an ID while preserving fallback completion."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    original = sale.payments[0]
    original.status = PaymentStatus.UNKNOWN
    session.commit()
    CheckoutService(session, binding).confirm_external_pos_payment(
        sale.id,
        owner_id=user.id,
        risk_acknowledged=True,
    )
    original.job.status = payment_jobs_module.PaymentJobStatus.QUEUED
    original.job.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(payment_jobs_module, "SessionLocal", factory)
    monkeypatch.setattr(payment_jobs_module, "get_checkout_queue", lambda: object())

    assert payment_jobs_module.recover_payment_jobs() == 0
    session.expire_all()
    recovered = session.get(Sale, sale.id)

    assert recovered.status is SaleStatus.COMPLETED
    assert recovered.payments[0].status is PaymentStatus.UNKNOWN
    assert recovered.payments[0].job.status is payment_jobs_module.PaymentJobStatus.FAILED
    assert session.query(StockMovement).count() == 1


def test_worker_health_reports_heartbeat_and_stalled_durable_jobs(
    session: Session,
    checkout_setup: tuple[
        User, CatalogVariant, Integration, FakeCheckoutProvider, CheckoutProviderBinding
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RQ liveness and PostgreSQL queue age remain separate observable facts."""
    user, variant, _, _, binding = checkout_setup
    sale = _sale_with_item(session, user, variant)
    sale = CheckoutService(session, binding).start(sale.id, owner_id=user.id)
    now = datetime.now(UTC)

    class QueueStub:
        name = "checkout"

    class WorkerStub:
        queues = [QueueStub()]
        last_heartbeat = now

    monkeypatch.setattr(sales_health_module.Worker, "all", lambda connection: [WorkerStub()])
    settings = Settings(
        jwt_secret="test",
        redis_url="redis://unused:6379/0",
        checkout_queued_job_stale_seconds=60,
        checkout_processing_job_stale_seconds=120,
        _env_file=None,
    )

    healthy = sales_health_module.collect_checkout_worker_health(session, settings, now=now)

    assert healthy.running is True
    assert healthy.queued_jobs == 1
    assert healthy.stalled_jobs == 0

    session.execute(
        payment_jobs_module.PaymentJob.__table__.update()
        .where(payment_jobs_module.PaymentJob.id == sale.payments[0].job.id)
        .values(created_at=now - timedelta(seconds=120))
    )
    session.commit()
    stale = sales_health_module.collect_checkout_worker_health(session, settings, now=now)
    assert stale.oldest_queued_age_seconds == 120
    assert stale.stalled_jobs == 1
    assert sale in list_checkout_operations(
        session,
        settings,
        user,
        state="attention",
        date_from=None,
        date_to=None,
        limit=100,
    )

    monkeypatch.setattr(sales_health_module.Worker, "all", lambda connection: [])
    missing = sales_health_module.collect_checkout_worker_health(session, settings, now=now)
    assert missing.running is False

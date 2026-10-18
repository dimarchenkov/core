from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.customers.customer import Customer
from core.customers.models import CustomerRecord
from core.customers.schemas import CustomerCreate
from core.customers.service import CustomerService
from core.identity.models import User
from core.intake.models import IntakeItemDraft  # noqa: F401
from core.rental.agreement_models import (
    RentalAgreementRecord,
    RentalAgreementSettingsRecord,
    RentalAgreementTemplateVersionRecord,
)
from core.rental.enums import (
    AssetCondition,
    AssetPurpose,
    RentalAvailability,
    RentalComponentVerificationResult,
    RentalSealStatus,
    RentalSealVerificationResult,
)
from core.rental.exceptions import RentalDomainError
from core.rental.lifecycle_schemas import (
    RentalAssetComponentCreate,
    RentalAssetComponentUpdate,
    RentalAssetSealCreate,
)
from core.rental.lifecycle_service import RentalLifecycleService
from core.rental.models import (
    RentalAssetComponentRecord,
    RentalAssetRecord,
    RentalAssetSealRecord,
    RentalDamageRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.order import RentalOrder
from core.rental.order_enums import (
    RentalOrderItemStatus,
    RentalOrderStatus,
    RentalReturnCondition,
    RentalTimingSource,
)
from core.rental.order_exceptions import (
    DuplicateRentalAssetError,
    ExpiredRentalSlotError,
    InvalidRentalOrderStateError,
)
from core.rental.order_schemas import (
    RentalComponentVerificationDraft,
    RentalDepositReceivedUpdate,
    RentalItemReturnDraft,
    RentalItemsReturn,
    RentalOrderCreate,
    RentalOrderIssue,
    RentalOrderItemCreate,
    RentalOrderRead,
    RentalOrderTariffConfigure,
    RentalOrderUpdate,
    RentalSealVerificationDraft,
)
from core.rental.order_service import (
    RentalAgreementRequiredError,
    RentalDepositUnconfirmedError,
    RentalOrderService,
    RentalTariffUnavailableError,
    RentalTariffVariantMismatchError,
)
from core.rental.tariff_enums import RentalTariffType
from core.rental.tariff_models import RentalTariffRecord
from core.rental.tariff_schemas import RentalTariffCreate, RentalTariffUpdate
from core.rental.tariff_service import RentalTariffService
from core.shared.db import Base, generate_uuid_v7

TODAY = date(2026, 10, 9)


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the complete persistence slice used by tariff-based Rental drafts."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            CustomerRecord.__table__,
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            CatalogVariantBarcode.__table__,
            RentalAssetRecord.__table__,
            RentalAssetComponentRecord.__table__,
            RentalAssetSealRecord.__table__,
            RentalTariffRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
            RentalDamageRecord.__table__,
            RentalAgreementSettingsRecord.__table__,
            RentalAgreementTemplateVersionRecord.__table__,
            RentalAgreementRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def customer(session: Session) -> Customer:
    """Create the existing Customer that starts every new Rental flow."""
    return CustomerService(session).create_customer(
        CustomerCreate(full_name="Иван Иванов", phone="+79991234567")
    )


@pytest.fixture
def rental_setup(
    session: Session,
) -> tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord]:
    """Create one Puzzi-like rentable Variant with SLOT and DURATION tariffs."""
    category = Category(title="Моющая техника", slug="cleaning")
    product = CatalogProduct(title="Karcher Puzzi 10/1", slug="puzzi", category=category)
    variant = CatalogVariant(
        product=product,
        title="Стандарт",
        sku="PUZZI-10-1",
        barcode="2000000000999",
        attributes={},
    )
    session.add_all([category, product, variant])
    session.flush()
    asset = RentalAssetRecord(
        asset_number="RENT-000123",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(asset)
    session.flush()
    tariffs = RentalTariffService(session)
    slot = tariffs.create(
        variant.id,
        RentalTariffCreate(
            name="Утро",
            type=RentalTariffType.SLOT,
            price="1900.00",
            slot_start=time(9),
            slot_end=time(14),
            grace_minutes=30,
            deposit_amount="5000.00",
            requires_agreement=True,
            valid_from=TODAY,
        ),
    )
    duration = tariffs.create(
        variant.id,
        RentalTariffCreate(
            name="5 часов",
            type=RentalTariffType.DURATION,
            price="1900.00",
            duration_minutes=300,
            grace_minutes=30,
            deposit_amount="5000.00",
            requires_agreement=True,
            valid_from=TODAY,
        ),
    )
    session.commit()
    return variant, asset, slot, duration


def _draft(session: Session, customer: Customer) -> RentalOrderRecord:
    """Start through the client-first API shape without commercial placeholders."""
    order = RentalOrderService(session).create_draft(RentalOrderCreate(customer_id=customer.id))
    record = session.get(RentalOrderRecord, order.id)
    assert record is not None
    return record


def _ready_without_agreement(
    session: Session,
    customer: Customer,
    variant: CatalogVariant,
    asset: RentalAssetRecord,
    *,
    name: str,
) -> RentalOrder:
    """Prepare one issueable order for identification snapshot tests."""
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name=name,
            type=RentalTariffType.DURATION,
            price="350.00",
            duration_minutes=180,
            deposit_amount="0.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    return RentalOrderService(session).add_tariff_item(
        _draft(session, customer).id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=tariff.id,
            rental_date=TODAY,
        ),
    )


def test_client_first_draft_uses_existing_customer_and_stays_incomplete(
    session: Session, customer: Customer
) -> None:
    order = RentalOrderService(session).create_draft(RentalOrderCreate(customer_id=customer.id))

    assert order.customer_id == customer.id
    assert order.status is RentalOrderStatus.DRAFT
    assert order.items == ()
    assert order.rental_total == Decimal("0.00")
    assert order.deposit_received is None
    assert order.deposit_received_confirmed is False
    assert order.recommended_deposit_total == Decimal("0.00")


def _order_with_snapshot_deposits(deposits: list[str]) -> RentalOrder:
    """Build a tariff-snapshot order without confirming physical receipt."""
    now = datetime.now(UTC)
    order = RentalOrder.create(
        order_id=generate_uuid_v7(),
        order_number="RORD-DEPOSIT",
        customer_id=generate_uuid_v7(),
        customer_name_snapshot="Иван Иванов",
        customer_phone_snapshot="+79990000000",
        planned_start_at=now,
        planned_return_at=now + timedelta(hours=3),
        deposit_amount=None,
    )
    for index, deposit in enumerate(deposits):
        order.add_configured_item(
            item_id=generate_uuid_v7(),
            rental_asset_id=generate_uuid_v7(),
            asset_number_snapshot=f"RENT-DEP-{index}",
            title_snapshot=f"Оборудование {index}",
            agreed_price=Decimal("300"),
            planned_rental_date=TODAY,
            planned_start_at=None,
            planned_return_at=None,
            deposit_amount=Decimal("999"),
            rental_tariff_id=generate_uuid_v7(),
            rental_tariff_version=1,
            timing_source=RentalTimingSource.TARIFF,
            tariff_snapshot={
                "tariff_type": "duration",
                "duration_minutes": 180,
                "deposit_amount": deposit,
            },
        )
    return order


@pytest.mark.parametrize(
    ("deposits", "expected"),
    [
        (["2000"], "2000.00"),
        (["2000", "2000"], "4000.00"),
        (["2000", "3000", "5000"], "10000.00"),
        (["0", "5000"], "5000.00"),
    ],
)
def test_required_deposit_total_sums_every_item_tariff_snapshot(
    deposits: list[str], expected: str
) -> None:
    """Contractual deposit is an exact snapshot sum, never an order-level default."""
    order = _order_with_snapshot_deposits(deposits)

    assert order.required_deposit_total == Decimal(expected)
    assert order.recommended_deposit_total == Decimal(expected)
    assert RentalOrderRead.model_validate(order).required_deposit_total == Decimal(expected)


@pytest.mark.parametrize("received", ["4000", "2000", "0"])
def test_actual_deposit_confirmation_accepts_suggested_custom_and_zero(
    received: str,
) -> None:
    """Operator confirmation persists the exact receipt, including explicit zero."""
    order = _order_with_snapshot_deposits(["2000", "2000"])

    assert order.required_deposit_total == Decimal("4000.00")
    assert order.deposit_received is None
    assert order.deposit_received_confirmed is False

    order.set_deposit_received(Decimal(received))
    response = RentalOrderRead.model_validate(order)

    assert response.deposit_received == Decimal(received).quantize(Decimal("0.01"))
    assert response.deposit_received_confirmed is True


def test_duration_ready_persists_date_without_starting_rental_clock(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, _, duration = rental_setup
    draft = _draft(session, customer)
    ready = RentalOrderService(session).add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=duration.id,
            rental_date=TODAY,
        ),
    )

    assert ready.status is RentalOrderStatus.READY
    item = ready.items[0]
    assert item.planned_rental_date == TODAY
    assert item.planned_start_at is None
    assert item.issued_at is None
    assert item.due_at is None
    assert item.rental_tariff_id == duration.id
    assert item.rental_tariff_version == 1
    assert item.recommended_deposit == Decimal("5000.00")
    assert ready.deposit_received is None
    assert ready.deposit_received_confirmed is False
    assert ready.required_deposit_total == Decimal("5000.00")
    assert ready.recommended_deposit_total == Decimal("5000.00")
    assert ready.rental_total == Decimal("1900.00")
    assert item.agreed_price == Decimal("1900.00")
    assert item.tariff_snapshot == {
        "tariff_id": str(duration.id),
        "tariff_lineage_id": str(duration.lineage_id),
        "tariff_version": 1,
        "tariff_name": "5 часов",
        "tariff_type": "duration",
        "tariff_price": "1900.00",
        "agreed_price": "1900.00",
        "price_overridden": False,
        "duration_minutes": 300,
        "slot_start": None,
        "slot_end": None,
        "grace_minutes": 30,
        "overdue_policy": "next_period",
        "overdue_unit_minutes": 300,
        "overdue_unit_price": "1900.00",
        "deposit_amount": "5000.00",
        "requires_agreement": True,
        "canonical_tariff_terms": duration.format_terms(),
        "timing_source": "tariff",
        "planned_rental_date": TODAY.isoformat(),
        "planned_start_at": None,
        "due_at": None,
    }
    session.refresh(asset)
    assert asset.availability is RentalAvailability.AVAILABLE
    assert ready.issued_at is None
    persisted = RentalOrderService(session).get(draft.id).items[0]
    assert persisted.planned_rental_date == TODAY
    assert persisted.issued_at is None
    assert persisted.due_at is None


def test_slot_uses_fixed_moscow_boundaries_for_selected_date(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, slot, _ = rental_setup
    draft = _draft(session, customer)

    ready = RentalOrderService(session).add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=slot.id,
            rental_date=TODAY,
        ),
    )

    assert ready.planned_start_at.isoformat() == "2026-10-09T09:00:00+03:00"
    assert ready.planned_return_at.isoformat() == "2026-10-09T14:00:00+03:00"
    assert ready.items[0].planned_rental_date == TODAY
    assert ready.items[0].issued_at is None


def test_actual_issue_starts_duration_but_does_not_shift_slot(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, first_asset, slot, duration = rental_setup
    second_asset = RentalAssetRecord(
        asset_number="RENT-000126",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(second_asset)
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=first_asset.id, tariff_id=slot.id, rental_date=TODAY
        ),
    )
    ready = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id, tariff_id=duration.id, rental_date=TODAY
        ),
    )
    slot_due = ready.items[0].due_at
    duration_snapshot = ready.items[1].tariff_snapshot
    issue_time = datetime(2026, 10, 9, 6, 37, tzinfo=UTC)

    ready.issue(issued_at=issue_time)

    assert ready.items[0].issued_at == issue_time
    assert ready.items[0].due_at == slot_due
    assert ready.items[1].issued_at == issue_time
    assert ready.items[1].due_at == datetime(2026, 10, 9, 11, 37, tzinfo=UTC)
    assert ready.items[1].tariff_snapshot == duration_snapshot


def test_expired_slot_cannot_be_issued(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, slot, _ = rental_setup
    draft = _draft(session, customer)
    ready = RentalOrderService(session).add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(rental_asset_id=asset.id, tariff_id=slot.id, rental_date=TODAY),
    )

    with pytest.raises(ExpiredRentalSlotError):
        ready.issue(issued_at=datetime(2026, 10, 9, 11, 1, tzinfo=UTC))

    assert ready.status is RentalOrderStatus.READY
    assert ready.items[0].issued_at is None


def test_service_issue_uses_server_time_is_idempotent_and_audited(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, asset, _, _ = rental_setup
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="Без договора",
            type=RentalTariffType.DURATION,
            price="350.00",
            duration_minutes=180,
            deposit_amount="5000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    actor = User(
        email="operator@example.com",
        full_name="Оператор",
        password_hash="not-used",
    )
    session.add(actor)
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    ready = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id, tariff_id=tariff.id, rental_date=TODAY
        ),
    )
    preview = service.issue_preview(ready.id)
    before = datetime.now(UTC)

    issued = service.issue(ready.id, actor_id=actor.id)
    after = datetime.now(UTC)

    assert preview.can_issue is True
    assert preview.items[0].due_at == preview.server_now + timedelta(minutes=180)
    assert before <= issued.items[0].issued_at <= after
    assert issued.status is RentalOrderStatus.ISSUED
    assert issued.items[0].due_at == issued.items[0].issued_at + timedelta(minutes=180)
    session.refresh(asset)
    assert asset.availability is RentalAvailability.RENTED
    item_record = session.get(RentalOrderItemRecord, issued.items[0].id)
    order_record = session.get(RentalOrderRecord, issued.id)
    assert item_record is not None and item_record.issued_by_id == actor.id
    assert order_record is not None and order_record.issued_by_id == actor.id

    repeated = service.issue(ready.id, actor_id=actor.id)
    assert repeated.issued_at.replace(tzinfo=UTC) == issued.issued_at
    assert repeated.items[0].issued_at.replace(tzinfo=UTC) == issued.items[0].issued_at


def test_return_preview_exposes_unconfirmed_receipt_without_assuming_required_total(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    """Return DTO keeps NULL distinct from an explicit zero receipt."""
    variant, asset, _, _ = rental_setup
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="Возврат без договора",
            type=RentalTariffType.DURATION,
            price="300.00",
            duration_minutes=180,
            grace_minutes=30,
            deposit_amount="5000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    service = RentalOrderService(session)
    ready = service.add_tariff_item(
        _draft(session, customer).id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=tariff.id,
            rental_date=TODAY,
        ),
    )
    issued = service.issue(ready.id)
    preview = service.preview_return(
        issued.id,
        RentalItemsReturn(
            items=[
                RentalItemReturnDraft(
                    item_id=issued.items[0].id,
                    condition=RentalReturnCondition.GOOD,
                )
            ]
        ),
    )

    assert issued.deposit_received is None
    assert issued.required_deposit_total == Decimal("5000.00")
    assert preview.required_deposit_total == Decimal("5000.00")
    assert preview.deposit_received is None
    assert preview.deposit_received_confirmed is False
    assert preview.provisional_deposit_remaining is None
    assert preview.deposit_to_refund is None

    command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[0].id,
                condition=RentalReturnCondition.GOOD,
            )
        ]
    )
    with pytest.raises(RentalDepositUnconfirmedError):
        service.return_items(issued.id, command)

    still_issued = service.get(issued.id)
    assert still_issued.status is RentalOrderStatus.ISSUED
    assert still_issued.items[0].status is RentalOrderItemStatus.ISSUED

    explicitly_zero = service.confirm_deposit_received(
        issued.id,
        RentalDepositReceivedUpdate(deposit_received=Decimal("0")),
    )
    assert explicitly_zero.deposit_received == Decimal("0.00")
    assert explicitly_zero.deposit_received_confirmed is True
    completed = service.return_items(issued.id, command)
    assert completed.status is RentalOrderStatus.CLOSED


def test_manual_issue_override_sets_duration_due_from_actual_time(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, asset, _, _ = rental_setup
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="Три часа без договора",
            type=RentalTariffType.DURATION,
            price="350.00",
            duration_minutes=180,
            deposit_amount="0.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    service = RentalOrderService(session)
    ready = service.add_tariff_item(
        _draft(session, customer).id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id, tariff_id=tariff.id, rental_date=TODAY
        ),
    )
    actual_issue_at = datetime.now(UTC) - timedelta(minutes=10)

    issued = service.issue(ready.id, RentalOrderIssue(issued_at=actual_issue_at))

    assert issued.items[0].issued_at == actual_issue_at
    assert issued.items[0].due_at == actual_issue_at + timedelta(minutes=180)


def test_manual_issue_override_and_agreement_block(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, _, duration = rental_setup
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    ready = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id, tariff_id=duration.id, rental_date=TODAY
        ),
    )
    preview = service.issue_preview(ready.id)

    assert preview.requires_agreement is True
    assert preview.can_issue is False
    with pytest.raises(RentalAgreementRequiredError):
        service.issue(
            ready.id,
            RentalOrderIssue(issued_at=datetime.now(UTC) - timedelta(minutes=10)),
        )
    assert service.get(ready.id).status is RentalOrderStatus.READY
    session.refresh(asset)
    assert asset.availability is RentalAvailability.AVAILABLE


def test_multi_item_issue_rolls_back_when_one_asset_is_unavailable(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, first_asset, _, _ = rental_setup
    second_asset = RentalAssetRecord(
        asset_number="RENT-000124",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(second_asset)
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="Без договора",
            type=RentalTariffType.DURATION,
            price="350.00",
            duration_minutes=180,
            deposit_amount="0.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=first_asset.id, tariff_id=tariff.id, rental_date=TODAY
        ),
    )
    ready = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id, tariff_id=tariff.id, rental_date=TODAY
        ),
    )
    second_asset.availability = RentalAvailability.RENTED
    session.commit()

    with pytest.raises(RentalDomainError):
        service.issue(ready.id)

    session.refresh(first_asset)
    assert first_asset.availability is RentalAvailability.AVAILABLE
    persisted = service.get(ready.id)
    assert persisted.status is RentalOrderStatus.READY
    assert all(item.issued_at is None for item in persisted.items)


def test_item_returns_are_server_calculated_partial_atomic_and_historical(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    """Partial and final returns preserve snapshot billing, deposit, and asset routing."""
    variant, first_asset, _, _ = rental_setup
    second_asset = RentalAssetRecord(
        asset_number="RENT-000124",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(second_asset)
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="3 часа",
            type=RentalTariffType.DURATION,
            price="300.00",
            duration_minutes=180,
            grace_minutes=30,
            deposit_amount="2000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    second_tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="3 часа — второй предмет",
            type=RentalTariffType.DURATION,
            price="300.00",
            duration_minutes=180,
            grace_minutes=30,
            deposit_amount="2000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    service.update_draft(
        draft.id,
        RentalOrderUpdate(deposit_received=Decimal("2000")),
    )
    service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=first_asset.id,
            tariff_id=tariff.id,
            rental_date=TODAY,
        ),
    )
    ready = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id,
            tariff_id=second_tariff.id,
            rental_date=TODAY,
        ),
    )
    assert ready.required_deposit_total == Decimal("4000.00")
    assert ready.deposit_received == Decimal("2000.00")
    issued = service.issue(ready.id)
    item_records = {item.id: session.get(RentalOrderItemRecord, item.id) for item in issued.items}
    for record in item_records.values():
        assert record is not None
        record.due_at = datetime.now(UTC) - timedelta(minutes=31)
    session.commit()

    first_command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[0].id,
                condition=RentalReturnCondition.GOOD,
            )
        ]
    )
    preview = service.preview_return(issued.id, first_command)
    repeated_preview = service.preview_return(issued.id, first_command)
    partial = service.return_items(issued.id, first_command)

    assert preview.is_partial is True
    assert preview.required_deposit_total == Decimal("4000.00")
    assert preview.deposit_received == Decimal("2000.00")
    assert repeated_preview.required_deposit_total == Decimal("4000.00")
    assert preview.items[0].overdue_charge == Decimal("300.00")
    assert preview.items[0].deposit_applied == Decimal("300.00")
    assert preview.deposit_to_refund is None
    assert partial.status is RentalOrderStatus.ISSUED
    assert partial.items[0].status is RentalOrderItemStatus.RETURNED
    assert partial.items[1].status is RentalOrderItemStatus.ISSUED
    assert partial.required_deposit_total == Decimal("4000.00")
    assert partial.deposit_applied_total == Decimal("300.00")
    session.refresh(first_asset)
    assert first_asset.availability is RentalAvailability.AVAILABLE

    tariff.deposit_amount = Decimal("5000.00")
    second_tariff.price = Decimal("999.00")
    second_tariff.deposit_amount = Decimal("5000.00")
    second_tariff.grace_minutes = 0
    session.commit()
    second_command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[1].id,
                condition=RentalReturnCondition.DAMAGED,
                damage_charge=Decimal("100"),
                notes="Трещина корпуса",
            )
        ]
    )
    final_preview = service.preview_return(issued.id, second_command)
    repeated_final_preview = service.preview_return(issued.id, second_command)
    completed = service.return_items(issued.id, second_command)

    assert final_preview.required_deposit_total == Decimal("4000.00")
    assert final_preview.deposit_received == Decimal("2000.00")
    assert repeated_final_preview.required_deposit_total == Decimal("4000.00")
    assert final_preview.items[0].billing_unit_price == Decimal("300.00")
    assert final_preview.items[0].overdue_charge == Decimal("300.00")
    assert final_preview.items[0].damage_charge == Decimal("100.00")
    assert final_preview.deposit_applied_by_return == Decimal("400.00")
    assert final_preview.deposit_to_refund == Decimal("1300.00")
    assert final_preview.remaining_amount_due == Decimal("0.00")
    assert completed.status is RentalOrderStatus.CLOSED
    assert completed.required_deposit_total == Decimal("4000.00")
    assert completed.deposit_settlement_finalized is True
    assert completed.deposit_to_refund == Decimal("1300.00")
    assert completed.remaining_amount_due == Decimal("0.00")
    session.refresh(second_asset)
    assert second_asset.availability is RentalAvailability.MAINTENANCE
    assert second_asset.condition is AssetCondition.DAMAGED
    assert session.query(RentalDamageRecord).count() == 1

    with pytest.raises(InvalidRentalOrderStateError):
        service.return_items(issued.id, second_command)


def test_unavailable_asset_and_wrong_variant_tariff_are_rejected(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, slot, _ = rental_setup
    draft = _draft(session, customer)
    asset.availability = RentalAvailability.RENTED
    session.commit()
    with pytest.raises(RentalDomainError):
        RentalOrderService(session).add_tariff_item(
            draft.id,
            RentalOrderTariffConfigure(
                rental_asset_id=asset.id, tariff_id=slot.id, rental_date=TODAY
            ),
        )

    asset.availability = RentalAvailability.AVAILABLE
    category = Category(title="Другое", slug="other")
    product = CatalogProduct(title="Другой", slug="other", category=category)
    variant = CatalogVariant(
        product=product, title="V", sku="OTHER", barcode="2000000000888", attributes={}
    )
    session.add_all([category, product, variant])
    session.flush()
    other_asset = RentalAssetRecord(
        asset_number="RENT-000999",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(other_asset)
    session.commit()
    with pytest.raises(RentalTariffVariantMismatchError):
        RentalOrderService(session).add_tariff_item(
            draft.id,
            RentalOrderTariffConfigure(
                rental_asset_id=other_asset.id, tariff_id=slot.id, rental_date=TODAY
            ),
        )


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    [(date(2026, 10, 10), None), (date(2026, 10, 1), date(2026, 10, 8))],
)
def test_future_and_expired_tariffs_are_rejected(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
    valid_from: date,
    valid_to: date | None,
) -> None:
    variant, asset, _, _ = rental_setup
    tariff = RentalTariffService(session).create(
        variant.id,
        RentalTariffCreate(
            name="Недоступный",
            type=RentalTariffType.DURATION,
            price="1000",
            duration_minutes=60,
            deposit_amount="0",
            valid_from=valid_from,
            valid_to=valid_to,
        ),
    )
    session.commit()
    draft = _draft(session, customer)

    with pytest.raises(RentalTariffUnavailableError):
        RentalOrderService(session).add_tariff_item(
            draft.id,
            RentalOrderTariffConfigure(
                rental_asset_id=asset.id,
                tariff_id=tariff.id,
                rental_date=TODAY,
            ),
        )


def test_manual_timing_and_price_override_do_not_mutate_tariff(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, _, duration = rental_setup
    draft = _draft(session, customer)
    original = duration.to_snapshot()

    ready = RentalOrderService(session).add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=duration.id,
            timing_source=RentalTimingSource.MANUAL,
            planned_start_at=datetime(2026, 10, 9, 11, 12, tzinfo=UTC),
            planned_return_at=datetime(2026, 10, 10, 9, 45, tzinfo=UTC),
            agreed_price="1750.00",
        ),
    )

    session.refresh(duration)
    assert duration.to_snapshot() == original
    item = ready.items[0]
    assert item.timing_source is RentalTimingSource.MANUAL
    assert item.agreed_price == Decimal("1750.00")
    assert item.tariff_snapshot is not None
    assert item.tariff_snapshot["tariff_price"] == "1900.00"
    assert item.tariff_snapshot["price_overridden"] is True


def test_snapshot_survives_tariff_version_archive_and_item_edit(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    _, asset, slot, duration = rental_setup
    draft = _draft(session, customer)
    service = RentalOrderService(session)
    first = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(rental_asset_id=asset.id, tariff_id=slot.id, rental_date=TODAY),
    )
    first_item_id = first.items[0].id
    first_snapshot = first.items[0].tariff_snapshot
    tariffs = RentalTariffService(session)
    tariffs.archive(slot.id)
    tariffs.create_version(
        duration.id,
        RentalTariffUpdate(
            name="5 часов",
            type=RentalTariffType.DURATION,
            price="2500.00",
            duration_minutes=300,
            grace_minutes=5,
            deposit_amount="9000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
        today=TODAY,
    )
    session.commit()

    unchanged = service.get(draft.id)
    assert unchanged.items[0].tariff_snapshot == first_snapshot
    assert unchanged.recommended_deposit_total == Decimal("5000.00")
    assert unchanged.items[0].tariff_snapshot is not None
    assert unchanged.items[0].tariff_snapshot["grace_minutes"] == 30
    assert unchanged.items[0].tariff_snapshot["requires_agreement"] is True

    replacement = tariffs.create(
        duration.variant_id,
        RentalTariffCreate(
            name="День",
            type=RentalTariffType.DURATION,
            price="2100.00",
            duration_minutes=240,
            grace_minutes=10,
            deposit_amount="6000.00",
            requires_agreement=False,
            valid_from=TODAY,
        ),
    )
    session.commit()
    replaced = service.update_tariff_item(
        draft.id,
        first_item_id,
        RentalOrderTariffConfigure(
            rental_asset_id=asset.id,
            tariff_id=replacement.id,
            rental_date=TODAY,
        ),
    )
    assert replaced.status is RentalOrderStatus.READY
    assert len(replaced.items) == 1
    assert replaced.items[0].rental_tariff_id == replacement.id
    assert replaced.items[0].tariff_snapshot is not None
    assert replaced.items[0].tariff_snapshot["tariff_name"] == "День"


def test_multiple_items_keep_independent_terms_and_derive_order_totals(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, first_asset, slot, duration = rental_setup
    second_asset = RentalAssetRecord(
        asset_number="RENT-000124",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(second_asset)
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)

    first = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=first_asset.id, tariff_id=slot.id, rental_date=TODAY
        ),
    )
    second = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id,
            tariff_id=duration.id,
            rental_date=TODAY,
            agreed_price="1700.00",
        ),
    )

    assert len(second.items) == 2
    assert second.items[0].id == first.items[0].id
    assert second.items[0].due_at.hour == 14
    assert second.items[1].planned_rental_date == TODAY
    assert second.items[1].issued_at is None
    assert second.items[1].due_at is None
    assert second.rental_total == Decimal("3600.00")
    assert second.recommended_deposit_total == Decimal("10000.00")
    assert second.requires_agreement is True
    assert second.status is RentalOrderStatus.READY

    first_snapshot = second.items[0].tariff_snapshot
    first_due = second.items[0].due_at
    edited = service.update_tariff_item(
        draft.id,
        second.items[1].id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id,
            tariff_id=duration.id,
            timing_source=RentalTimingSource.MANUAL,
            planned_start_at=datetime(2026, 10, 10, 8, tzinfo=UTC),
            planned_return_at=datetime(2026, 10, 10, 10, tzinfo=UTC),
        ),
    )
    assert edited.items[0].tariff_snapshot == first_snapshot
    assert edited.items[0].due_at == first_due

    with pytest.raises(DuplicateRentalAssetError):
        service.add_tariff_item(
            draft.id,
            RentalOrderTariffConfigure(
                rental_asset_id=first_asset.id,
                tariff_id=duration.id,
                rental_date=TODAY,
            ),
        )

    after_remove = service.remove_item(draft.id, second.items[1].id)
    assert len(after_remove.items) == 1
    assert after_remove.status is RentalOrderStatus.READY
    empty = service.remove_item(draft.id, after_remove.items[0].id)
    assert empty.items == ()
    assert empty.status is RentalOrderStatus.DRAFT


def test_incomplete_legacy_sibling_prevents_ready(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    variant, first_asset, _, duration = rental_setup
    second_asset = RentalAssetRecord(
        asset_number="RENT-000125",
        variant_id=variant.id,
        purpose=AssetPurpose.RENTAL,
        condition=AssetCondition.NEW,
        availability=RentalAvailability.AVAILABLE,
    )
    session.add(second_asset)
    session.commit()
    service = RentalOrderService(session)
    draft = _draft(session, customer)
    incomplete = service.add_item(
        draft.id,
        RentalOrderItemCreate(rental_asset_id=first_asset.id, agreed_price="100.00"),
    )
    order = service.add_tariff_item(
        draft.id,
        RentalOrderTariffConfigure(
            rental_asset_id=second_asset.id,
            tariff_id=duration.id,
            rental_date=TODAY,
        ),
    )

    assert order.status is RentalOrderStatus.DRAFT
    ready = service.remove_item(draft.id, incomplete.items[0].id)
    assert ready.status is RentalOrderStatus.READY


@pytest.mark.parametrize(
    ("serial_number", "seal_count"),
    [(None, 0), ("SN-001", 0), (None, 1), ("SN-003", 3)],
)
def test_issue_freezes_optional_serial_and_active_seals(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
    serial_number: str | None,
    seal_count: int,
) -> None:
    """Issue accepts empty identification and snapshots every active seal when present."""
    variant, asset, _, _ = rental_setup
    asset.serial_number = serial_number
    session.add_all(
        [
            RentalAssetSealRecord(
                rental_asset_id=asset.id,
                location=f"Точка {index + 1}",
                seal_number=f"00184{index}",
                status=RentalSealStatus.ACTIVE,
                installed_at=datetime.now(UTC),
            )
            for index in range(seal_count)
        ]
    )
    session.commit()
    ready = _ready_without_agreement(
        session, customer, variant, asset, name=f"Снимок {seal_count}"
    )

    issued = RentalOrderService(session).issue(ready.id)
    snapshot = issued.items[0].asset_identification_snapshot

    assert snapshot is not None
    assert snapshot["asset_code"] == asset.asset_number
    assert snapshot["serial_number"] == serial_number
    assert len(snapshot["seals"]) == seal_count


@pytest.mark.parametrize(
    ("verification_result", "verification_note"),
    [
        (RentalSealVerificationResult.OK, None),
        (RentalSealVerificationResult.MISMATCH, "На месте установлена другая пломба"),
    ],
)
def test_replaced_seal_preserves_history_and_old_issue_snapshot_drives_return(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
    verification_result: RentalSealVerificationResult,
    verification_note: str | None,
) -> None:
    """Later asset edits cannot rewrite expected identification or return evidence."""
    variant, asset, _, _ = rental_setup
    actor = User(
        email="seal-operator@example.com",
        full_name="Оператор пломб",
        password_hash="not-used",
    )
    session.add(actor)
    asset.serial_number = "SERIAL-OLD"
    session.commit()
    lifecycle = RentalLifecycleService(session)
    old_seal = lifecycle.add_seal(
        asset.id,
        RentalAssetSealCreate(location="Корпус", seal_number="001842"),
        actor_id=actor.id,
    )
    ready = _ready_without_agreement(
        session, customer, variant, asset, name="История пломб"
    )
    orders = RentalOrderService(session)
    orders.update_draft(
        ready.id,
        RentalOrderUpdate(deposit_received=Decimal("0")),
    )
    issued = orders.issue(ready.id)

    asset.serial_number = "SERIAL-NEW"
    session.commit()
    new_seal = lifecycle.add_seal(
        asset.id,
        RentalAssetSealCreate(location="Корпус", seal_number="009999"),
        actor_id=actor.id,
    )
    session.expire_all()
    old_record = session.get(RentalAssetSealRecord, old_seal.id)

    assert old_record is not None
    assert old_record.status is RentalSealStatus.REMOVED
    assert old_record.removed_at is not None
    assert new_seal.status is RentalSealStatus.ACTIVE
    persisted = orders.get(issued.id)
    assert persisted.items[0].asset_identification_snapshot == {
        "asset_code": asset.asset_number,
        "serial_number": "SERIAL-OLD",
        "seals": [{"location": "Корпус", "seal_number": "001842"}],
        "components": [],
    }

    command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[0].id,
                condition=RentalReturnCondition.GOOD,
                seal_verifications=[
                    RentalSealVerificationDraft(
                        location="Корпус",
                        seal_number="001842",
                        result=verification_result,
                        note=verification_note,
                    )
                ],
            )
        ]
    )
    preview = orders.preview_return(issued.id, command)
    returned = orders.return_items(issued.id, command)

    assert preview.items[0].asset_identification_snapshot["serial_number"] == "SERIAL-OLD"
    assert preview.items[0].seal_verifications == [
        {
            "location": "Корпус",
            "expected_seal_number": "001842",
            "result": verification_result.value,
            "note": verification_note,
        }
    ]
    assert returned.items[0].seal_verification_snapshot == preview.items[0].seal_verifications


def test_seal_problem_requires_note() -> None:
    """A non-OK seal observation cannot be submitted without operator context."""
    with pytest.raises(ValueError, match="note is required"):
        RentalSealVerificationDraft(
            location="Корпус",
            seal_number="001842",
            result=RentalSealVerificationResult.MISMATCH,
        )


def test_issue_freezes_ordered_components_and_all_present_return_uses_snapshot(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    """Later additions and removals cannot change the checklist of an issued rental."""
    variant, asset, _, _ = rental_setup
    actor = User(
        email="components@example.com",
        full_name="Оператор комплектности",
        password_hash="not-used",
    )
    session.add(actor)
    session.commit()
    lifecycle = RentalLifecycleService(session)
    tank = lifecycle.add_component(
        asset.id, RentalAssetComponentCreate(name="Бак"), actor_id=actor.id
    )
    hose = lifecycle.add_component(
        asset.id,
        RentalAssetComponentCreate(name="Шланг", note="Синий"),
        actor_id=actor.id,
    )
    orders = RentalOrderService(session)
    ready = _ready_without_agreement(
        session, customer, variant, asset, name="Комплектность"
    )
    orders.update_draft(ready.id, RentalOrderUpdate(deposit_received=Decimal("0")))
    issued = orders.issue(ready.id)

    lifecycle.add_component(
        asset.id, RentalAssetComponentCreate(name="Новая насадка"), actor_id=actor.id
    )
    lifecycle.deactivate_component(asset.id, tank.id, actor_id=actor.id)
    persisted = orders.get(issued.id)
    snapshot = persisted.items[0].asset_identification_snapshot

    assert snapshot is not None
    assert snapshot["components"] == [
        {"component_id": str(tank.id), "name": "Бак", "note": None},
        {"component_id": str(hose.id), "name": "Шланг", "note": "Синий"},
    ]

    command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[0].id,
                condition=RentalReturnCondition.GOOD,
                component_verifications=[
                    RentalComponentVerificationDraft(
                        component_id=component["component_id"],
                        name=component["name"],
                        result=RentalComponentVerificationResult.PRESENT,
                    )
                    for component in snapshot["components"]
                ],
            )
        ]
    )
    preview = orders.preview_return(issued.id, command)
    returned = orders.return_items(issued.id, command)

    assert preview.items[0].condition is RentalReturnCondition.GOOD
    assert all(
        value["result"] == RentalComponentVerificationResult.PRESENT.value
        for value in preview.items[0].component_verifications
    )
    assert returned.items[0].component_verification_snapshot == (
        preview.items[0].component_verifications
    )


def test_missing_component_requires_note_and_maps_to_existing_incomplete_flow(
    session: Session,
    customer: Customer,
    rental_setup: tuple[CatalogVariant, RentalAssetRecord, RentalTariffRecord, RentalTariffRecord],
) -> None:
    """Missing checklist parts reuse INCOMPLETE and preserve immutable return evidence."""
    variant, asset, _, _ = rental_setup
    actor = User(
        email="missing-component@example.com",
        full_name="Оператор возврата",
        password_hash="not-used",
    )
    session.add(actor)
    session.commit()
    lifecycle = RentalLifecycleService(session)
    nozzle = lifecycle.add_component(
        asset.id,
        RentalAssetComponentCreate(name="Насадка для пола"),
        actor_id=actor.id,
    )
    orders = RentalOrderService(session)
    ready = _ready_without_agreement(
        session, customer, variant, asset, name="Неполный комплект"
    )
    orders.update_draft(ready.id, RentalOrderUpdate(deposit_received=Decimal("0")))
    issued = orders.issue(ready.id)

    with pytest.raises(ValueError, match="note is required"):
        RentalComponentVerificationDraft(
            component_id=nozzle.id,
            name="Насадка для пола",
            result=RentalComponentVerificationResult.MISSING,
        )

    command = RentalItemsReturn(
        items=[
            RentalItemReturnDraft(
                item_id=issued.items[0].id,
                condition=RentalReturnCondition.GOOD,
                component_verifications=[
                    RentalComponentVerificationDraft(
                        component_id=nozzle.id,
                        name="Насадка для пола",
                        result=RentalComponentVerificationResult.MISSING,
                        note="Клиент не вернул насадку",
                    )
                ],
            )
        ]
    )
    preview = orders.preview_return(issued.id, command)
    returned = orders.return_items(issued.id, command)
    lifecycle.update_component(
        asset.id,
        nozzle.id,
        RentalAssetComponentUpdate(name="Переименованная насадка"),
        actor_id=actor.id,
    )
    historical = orders.get(issued.id).items[0]

    assert preview.items[0].condition is RentalReturnCondition.INCOMPLETE
    assert preview.items[0].notes == "Насадка для пола: Клиент не вернул насадку"
    assert returned.items[0].return_condition is RentalReturnCondition.INCOMPLETE
    assert asset.availability is RentalAvailability.MAINTENANCE
    assert historical.asset_identification_snapshot["components"][0]["name"] == (
        "Насадка для пола"
    )
    assert historical.component_verification_snapshot == [
        {
            "component_id": str(nozzle.id),
            "name": "Насадка для пола",
            "expected_note": None,
            "result": "missing",
            "note": "Клиент не вернул насадку",
        }
    ]

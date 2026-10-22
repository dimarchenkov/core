from __future__ import annotations

from collections.abc import Generator
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import core.main  # noqa: F401 -- register complete model metadata
from core.analytics.read_service import SalesAnalyticsReadService, resolve_sales_period
from core.analytics.schemas import SalesPeriodPreset
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.identity.models import User
from core.integrations.aqsi.incident_recovery import (
    ALREADY_LINKED,
    CONFIRMED_LINKS,
    INCIDENT_DOCS,
    JsonObject,
    _snapshot,
    recover_receipts,
)
from core.integrations.checkout import CheckoutProviderFactory
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.inventory.models import StockMovement
from core.inventory.service import InventoryService
from core.sales.checkout import CheckoutService
from core.sales.enums import (
    FiscalizationStatus,
    PaymentJobStatus,
    PaymentMethod,
    PaymentStatus,
    SaleItemSource,
    SaleStatus,
)
from core.sales.models import Fiscalization, PaymentAttempt, PaymentJob, Sale, SaleItem
from core.sales.schemas import PaymentAttemptRead
from core.shared.db import Base


@pytest.fixture
def recovery_case() -> Generator[tuple[Session, JsonObject]]:
    """Synthetic 22-receipt incident; no production/card data enters the test suite."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        user = User(email="recovery@example.com", full_name="Operator", password_hash="unused")
        integration = Integration(name="AQSI", provider=IntegrationProvider.AQSI)
        product = CatalogProduct(title="Product", slug="recovery",
                                 category=Category(title="Recovery", slug="recovery"))
        variant = CatalogVariant(product=product, title="Variant", sku="RECOVERY",
                                 barcode="RECOVERY", attributes={})
        session.add_all([user, integration, variant])
        session.flush()
        receipts, slips = [], []
        amounts = [
            150,
            500,
            370,
            150,
            90,
            247,
            237.5,
            200,
            200,
            270,
            250,
            100,
            180,
            270,
            1370,
            40,
            200,
            150,
            8500,
            150,
            6910,
            380,
        ]
        for doc, amount in zip(sorted(INCIDENT_DOCS), amounts, strict=True):
            cents = int(Decimal(str(amount)) * 100)
            cash = 191000 if doc == 1687 else 0
            slip = {
                "id": str(uuid4()),
                "content": {
                    "type": "purchase",
                    "responseCode": "000",
                    "amount": cents - cash,
                },
            }
            slips.append(slip)
            if doc in {1673, 1676, 1687}:
                slip["content"].update(responseCode="SUCCESS", iface="sbp")
            parts = [{"type": 1, "amount": cents - cash, "slip": deepcopy(slip)}]
            if cash:
                parts.append({"type": 0, "amount": cash})
            receipt = {
                "id": str(uuid4()),
                "device": {"id": 123},
                "isNonFiscal": False,
                "info": {
                    "typeId": 1,
                    "dateTime": "2026-10-20T15:00:00+03:00",
                    "docInfo": {"docNumber": doc, "docFiscalAttributeInt": 123},
                    "sum": cents,
                    "amounts": {"cash": cash, "cashless": cents - cash},
                },
                "positions": [
                    {
                        "externalId": str(variant.id) if doc == 1677 else "",
                        "info": {
                            "name": "Variant",
                            "quantity": 1,
                            "finalPrice": cents,
                            "totalAmount": cents,
                        },
                    }
                ],
                "payments": parts,
            }
            receipts.append(receipt)
            number = (ALREADY_LINKED | CONFIRMED_LINKS).get(doc)
            if number is None:
                continue
            completed = doc in ALREADY_LINKED or number == 110
            sale = Sale(
                sale_number=number,
                owner_id=user.id,
                total_amount=Decimal(str(amount)),
                subtotal_amount=Decimal(str(amount)),
                discount_amount=Decimal(0),
                status=SaleStatus.COMPLETED
                if completed
                else (SaleStatus.CANCELLED if number == 112 else SaleStatus.PAYMENT_PENDING),
                completed_at=datetime(2026, 10, 20, 12, tzinfo=UTC) if completed else None,
            )
            sale.items.append(
                SaleItem(
                    source=SaleItemSource.CATALOG,
                    variant_id=variant.id,
                    display_label_snapshot="Original basket",
                    quantity=1,
                    unit_price=Decimal(str(amount)),
                    line_total=Decimal(str(amount)),
                    fiscal_line_total=Decimal(str(amount)),
                    currency="RUB",
                    fiscal_allocations=[{"quantity": 1, "unit_price": str(amount)}],
                )
            )
            session.add(sale)
            session.flush()
            payment = PaymentAttempt(
                sale_id=sale.id,
                integration_id=None if number == 110 else integration.id,
                provider=None if number == 110 else IntegrationProvider.AQSI,
                payment_method=PaymentMethod.CASH if number == 110 else PaymentMethod.CARD,
                requested_amount=Decimal(str(amount)),
                currency="RUB",
                attempt_number=1,
                fiscalization_required=number != 110,
                idempotency_key=f"old-{number}",
                status=PaymentStatus.SUCCEEDED
                if completed
                else (PaymentStatus.FAILED if number == 112 else PaymentStatus.PENDING),
                external_id=f"old-operation-{number}" if number != 110 else None,
                provider_metadata={"original": True},
            )
            sale.payments.append(payment)
            session.flush()
            if completed:
                sale.fiscalization = Fiscalization(
                    sale_id=sale.id,
                    payment_attempt_id=payment.id,
                    integration_id=None if number == 110 else integration.id,
                    provider=None if number == 110 else IntegrationProvider.AQSI,
                    status=FiscalizationStatus.SKIPPED
                    if number == 110
                    else FiscalizationStatus.SUCCEEDED,
                    external_receipt_id=None if number == 110 else receipt["id"],
                    idempotency_key=f"fiscal-{number}",
                    fiscal_amount=Decimal(str(amount)),
                    currency="RUB",
                    provider_metadata={"old": True},
                )
                InventoryService(session).create_sale_movements_once(
                    sale.id, [(variant.id, 1)], actor_id=user.id
                )
                sale.inventory_posted_at = datetime.now(UTC)
            if number == 109:
                sale.payment_jobs.append(
                    PaymentJob(
                        sale_id=sale.id,
                        payment_attempt_id=payment.id,
                        status=PaymentJobStatus.FAILED,
                        last_error="MANUAL_RECONCILIATION_HOLD",
                    )
                )
        session.commit()
        session.expire_all()
        evidence = {
            "date": "2026-10-20",
            "timezone": "Europe/Moscow",
            "integration_id": str(integration.id),
            "device_id": 123,
            "receipts": receipts,
            "slips": slips,
        }
        for model, key in (
            (Sale, "core_sales"),
            (SaleItem, "core_sale_items"),
            (PaymentAttempt, "core_payment_attempts"),
            (Fiscalization, "core_fiscalizations"),
        ):
            evidence[key] = [_snapshot(row) for row in session.scalars(select(model))]
        yield session, evidence


def test_recovery_preserves_history_inventory_and_is_idempotent(
    recovery_case: tuple[Session, JsonObject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, evidence = recovery_case

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Recovery must not construct payment/fiscal providers")

    monkeypatch.setattr(CheckoutProviderFactory, "__init__", forbidden)
    before_items = [_snapshot(i) for i in session.scalars(select(SaleItem))]
    result = recover_receipts(session, evidence, "digest")
    session.commit()
    assert [r["action"] for r in result].count("imported") == 16
    assert [r["action"] for r in result].count("linked") == 3
    assert session.scalar(select(func.count(Sale.id))) == 22
    assert session.scalar(select(func.count(StockMovement.id))) == 7
    for before in before_items:
        assert _snapshot(session.get(SaleItem, UUID(before["id"]))) == before
    sale110 = session.scalar(select(Sale).where(Sale.sale_number == 110))
    assert sale110.payments[0].status is PaymentStatus.SUCCEEDED
    assert sale110.payments[0].accounting_superseded
    assert PaymentAttemptRead.model_validate(sale110.payments[0]).accounting_superseded
    assert CheckoutService._successful_payment(sale110).payment_method is PaymentMethod.CARD
    assert (
        sale110.fiscalization.provider_metadata["prior_records"]["fiscalization"]["status"]
        == "skipped"
    )
    sale109 = session.scalar(select(Sale).where(Sale.sale_number == 109))
    assert sale109.payments[0].status is PaymentStatus.PENDING
    assert sale109.payments[0].external_id == "old-operation-109"
    assert sale109.payment_jobs[0].status is PaymentJobStatus.COMPLETED
    assert sale109.payments[-1].external_id is None  # slip is not an operation ID
    sale112 = session.scalar(select(Sale).where(Sale.sale_number == 112))
    assert sale112.status is SaleStatus.COMPLETED
    assert sale112.fiscalization.provider_metadata["prior_records"]["sale"]["status"] == "cancelled"
    assert sale112.completed_at.date() == date(2026, 10, 20)
    counts = [
        session.scalar(select(func.count(m.id)))
        for m in (Sale, SaleItem, PaymentAttempt, Fiscalization, StockMovement)
    ]
    again = recover_receipts(session, evidence, "digest")
    assert all(r["action"] == "already_recorded" for r in again)
    assert not session.dirty and not session.new
    assert counts == [
        session.scalar(select(func.count(m.id)))
        for m in (Sale, SaleItem, PaymentAttempt, Fiscalization, StockMovement)
    ]


def test_mixed_payment_and_superseded_cash_do_not_double_revenue(
    recovery_case: tuple[Session, JsonObject],
) -> None:
    session, evidence = recovery_case
    recover_receipts(session, evidence, "digest")
    period = resolve_sales_period(
        SalesPeriodPreset.CUSTOM,
        "Europe/Moscow",
        start_date=date(2026, 10, 20),
        end_date=date(2026, 10, 20),
    )
    methods = SalesAnalyticsReadService(session, "Europe/Moscow").payment_methods(period)
    assert {m.payment_method: m.revenue for m in methods} == {
        "cash": Decimal("1910.00"),
        "card": Decimal("19004.50"),
    }
    assert session.scalar(select(func.sum(Sale.total_amount))) == Decimal("20914.50")


@pytest.mark.parametrize("error", ["amount", "slip", "device", "date", "variant", "changed"])
def test_invalid_or_changed_evidence_is_atomic(
    recovery_case: tuple[Session, JsonObject],
    error: str,
) -> None:
    session, original = recovery_case
    evidence = deepcopy(original)
    receipt = evidence["receipts"][-1]
    if error == "amount":
        receipt["positions"][0]["info"]["totalAmount"] += 1
    elif error == "slip":
        receipt["payments"][0]["slip"]["content"]["responseCode"] = "123"
    elif error == "device":
        receipt["device"]["id"] = 999
    elif error == "date":
        receipt["info"]["dateTime"] = "2026-10-21T00:01:00+03:00"
    elif error == "variant":
        receipt["positions"][0]["externalId"] = str(uuid4())
    else:
        sale = session.scalar(select(Sale).where(Sale.sale_number == 110))
        sale.items[0].display_label_snapshot = "Changed after approval"
        session.commit()
    with pytest.raises(ValueError):
        recover_receipts(session, evidence, "digest")
    session.rollback()
    assert session.scalar(select(func.count(Sale.id))) == 6
    assert session.scalar(select(func.count(StockMovement.id))) == 4
    assert (
        session.scalar(select(Sale).where(Sale.sale_number == 109)).status
        is SaleStatus.PAYMENT_PENDING
    )

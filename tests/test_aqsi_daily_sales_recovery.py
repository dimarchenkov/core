from __future__ import annotations

from collections.abc import Generator
from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import core.main  # noqa: F401 -- register complete model metadata
from core.analytics.read_service import SalesAnalyticsReadService, resolve_sales_period
from core.analytics.schemas import SalesPeriodPreset
from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.identity.models import User
from core.integrations.aqsi.daily_sales_recovery import JsonObject, recover_daily_sales
from core.integrations.checkout import CheckoutProviderFactory
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.inventory.models import StockMovement
from core.sales.enums import FiscalizationStatus, PaymentMethod, PaymentStatus, SaleStatus
from core.sales.models import Fiscalization, PaymentAttempt, Sale
from core.shared.db import Base


@pytest.fixture
def daily_case() -> Generator[tuple[Session, JsonObject, JsonObject]]:
    """Build synthetic provider evidence matching the approved day-level invariants."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        user = User(email="daily-recovery@example.com", full_name="Operator", password_hash="x")
        integration = Integration(name="AQSI", provider=IntegrationProvider.AQSI)
        category = Category(title="Recovery", slug="daily-recovery")
        receipt_product = CatalogProduct(
            title="Receipt product", slug="receipt-product", category=category
        )
        operator_product = CatalogProduct(
            title="Operator product", slug="operator-product", category=category
        )
        receipt_variant = CatalogVariant(
            product=receipt_product, title="Variant", sku="DAY-RECEIPT", barcode="DAY-1"
        )
        operator_variant = CatalogVariant(
            product=operator_product, title="Variant", sku="DAY-OPERATOR", barcode="DAY-2"
        )
        session.add_all([user, integration, receipt_variant, operator_variant])
        session.flush()

        receipts: list[JsonObject] = []
        slips: list[JsonObject] = []
        amounts = [10000] * 12 + [258000]
        for doc, cents in zip(range(1693, 1706), amounts, strict=True):
            slip = {
                "id": str(uuid4()),
                "content": {
                    "type": "purchase",
                    "responseCode": "000",
                    "amount": cents,
                    "iface": "contactless",
                },
            }
            slips.append(deepcopy(slip))
            receipts.append(
                {
                    "id": str(uuid4()),
                    "device": {"id": 748887},
                    "isNonFiscal": False,
                    "info": {
                        "typeId": 1,
                        "dateTime": f"2026-10-21T12:{doc - 1693:02d}:00+03:00",
                        "docInfo": {"docNumber": doc, "docFiscalAttributeInt": doc * 10},
                        "sum": cents,
                        "amounts": {
                            "cash": 0,
                            "cashless": cents,
                            "prepayment": 0,
                            "credit": 0,
                            "consideration": 0,
                        },
                    },
                    "positions": [
                        {
                            "externalId": str(receipt_variant.id) if doc == 1693 else "",
                            "info": {
                                "name": f"Receipt line {doc}",
                                "quantity": 1,
                                "finalPrice": cents,
                                "totalAmount": cents,
                            },
                        }
                    ],
                    "payments": [{"type": 1, "amount": cents, "slip": deepcopy(slip)}],
                }
            )
        evidence = {
            "date": "2026-10-21",
            "timezone": "Europe/Moscow",
            "integration_id": str(integration.id),
            "receipts": receipts,
            "slips": slips,
        }
        prices = [50, 150, 250, 100, 350, 100, 250, 220, 120, 100, 350, 50, 350]
        sales = []
        for sequence, price in enumerate(prices, start=1):
            quantity = 2 if sequence == 12 else 1
            row: JsonObject = {
                "sequence": sequence,
                "label": f"Operator line {sequence}",
                "quantity": quantity,
                "unit_price": str(price),
                "source": "catalog" if sequence in {11, 12} else "manual",
            }
            if sequence in {11, 12}:
                row["variant_id"] = str(operator_variant.id)
            sales.append(row)
        manifest = {
            "date": "2026-10-21",
            "timezone": "Europe/Moscow",
            "owner_id": str(user.id),
            "mode": "cash_without_receipt",
            "sales": sales,
        }
        session.commit()
        yield session, evidence, manifest


def test_daily_recovery_imports_facts_without_provider_calls_and_is_idempotent(
    daily_case: tuple[Session, JsonObject, JsonObject],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, evidence, manifest = daily_case

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Recovery must not construct payment/fiscal providers")

    monkeypatch.setattr(CheckoutProviderFactory, "__init__", forbidden)
    result = recover_daily_sales(session, evidence, "evidence-digest", manifest, "manifest-digest")
    session.commit()
    assert len(result) == 26
    assert all(row["action"] == "imported" for row in result)
    assert session.scalar(select(func.count(Sale.id))) == 26
    assert session.scalar(select(func.count(PaymentAttempt.id))) == 26
    assert session.scalar(select(func.count(Fiscalization.id))) == 26
    assert session.scalar(select(func.count(StockMovement.id))) == 3
    assert session.scalar(select(func.sum(Sale.total_amount))) == Decimal("6270.00")
    assert not session.scalars(select(Sale).where(Sale.status != SaleStatus.COMPLETED)).all()

    cash = session.scalars(
        select(PaymentAttempt).where(PaymentAttempt.payment_method == PaymentMethod.CASH)
    ).all()
    assert len(cash) == 13
    assert all(
        payment.status is PaymentStatus.SUCCEEDED
        and payment.integration_id is None
        and payment.provider is None
        and not payment.fiscalization_required
        for payment in cash
    )
    assert all(payment.sale.fiscalization.status is FiscalizationStatus.SKIPPED for payment in cash)

    counts = [
        session.scalar(select(func.count(model.id)))
        for model in (Sale, PaymentAttempt, Fiscalization, StockMovement)
    ]
    repeated = recover_daily_sales(
        session, evidence, "evidence-digest", manifest, "manifest-digest"
    )
    assert all(row["action"] == "already_recorded" for row in repeated)
    assert counts == [
        session.scalar(select(func.count(model.id)))
        for model in (Sale, PaymentAttempt, Fiscalization, StockMovement)
    ]


def test_daily_recovery_is_visible_on_historical_day(
    daily_case: tuple[Session, JsonObject, JsonObject],
) -> None:
    session, evidence, manifest = daily_case
    recover_daily_sales(session, evidence, "evidence-digest", manifest, "manifest-digest")
    session.commit()
    period = resolve_sales_period(
        SalesPeriodPreset.CUSTOM,
        "Europe/Moscow",
        start_date=date(2026, 10, 21),
        end_date=date(2026, 10, 21),
    )
    methods = SalesAnalyticsReadService(session, "Europe/Moscow").payment_methods(period)
    assert {row.payment_method: row.revenue for row in methods} == {
        "card": Decimal("3780.00"),
        "cash": Decimal("2490.00"),
    }


@pytest.mark.parametrize("bad_input", ["receipt_total", "slip", "operator_total", "digest"])
def test_daily_recovery_rejects_changed_or_conflicting_input_atomically(
    daily_case: tuple[Session, JsonObject, JsonObject],
    bad_input: str,
) -> None:
    session, original_evidence, original_manifest = daily_case
    evidence, manifest = deepcopy(original_evidence), deepcopy(original_manifest)
    if bad_input == "receipt_total":
        evidence["receipts"][0]["positions"][0]["info"]["totalAmount"] += 1
    elif bad_input == "slip":
        evidence["receipts"][0]["payments"][0]["slip"]["content"]["responseCode"] = "123"
    elif bad_input == "operator_total":
        manifest["sales"][0]["unit_price"] = "51"
    else:
        recover_daily_sales(session, evidence, "digest-a", manifest, "manifest-digest")
        session.commit()
        with pytest.raises(ValueError, match="does not match"):
            recover_daily_sales(session, evidence, "digest-b", manifest, "manifest-digest")
        return
    with pytest.raises(ValueError):
        recover_daily_sales(session, evidence, "evidence-digest", manifest, "manifest-digest")
    session.rollback()
    assert session.scalar(select(func.count(Sale.id))) == 0

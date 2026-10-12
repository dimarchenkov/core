from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.intake.models import IntakeItemDraft  # noqa: F401
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord
from core.rental.tariff_enums import RentalTariffStatus, RentalTariffType
from core.rental.tariff_models import RentalTariffRecord
from core.rental.tariff_schemas import RentalTariffCreate, RentalTariffRead, RentalTariffUpdate
from core.rental.tariff_service import (
    RentableVariantNotFoundError,
    RentalTariffDeleteBlockedError,
    RentalTariffService,
)
from core.shared.db import Base

TODAY = date(2026, 10, 9)


@pytest.fixture
def session() -> Generator[Session]:
    """Provide isolated tariff persistence using the production SQLAlchemy model."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            CatalogVariantBarcode.__table__,
            Price.__table__,
            RentalAssetRecord.__table__,
            RentalTariffRecord.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def rentable_variant(session: Session) -> CatalogVariant:
    """Create one CatalogVariant with a physical RentalAsset allocation."""
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
    session.add(
        RentalAssetRecord(
            asset_number="RENT-000099",
            variant_id=variant.id,
            purpose=AssetPurpose.RENTAL,
            condition=AssetCondition.NEW,
            availability=RentalAvailability.AVAILABLE,
            acquisition_cost=Decimal("70000.00"),
        )
    )
    session.commit()
    return variant


def duration_data(**changes: object) -> RentalTariffCreate:
    """Build valid duration input while allowing focused overrides."""
    values: dict[str, object] = {
        "name": "5 часов",
        "type": RentalTariffType.DURATION,
        "price": "2200.00",
        "duration_minutes": 300,
        "grace_minutes": 30,
        "deposit_amount": "5000.00",
        "requires_agreement": True,
        "valid_from": TODAY,
    }
    values.update(changes)
    return RentalTariffCreate.model_validate(values)


def slot_data(**changes: object) -> RentalTariffCreate:
    """Build valid daytime slot input while allowing focused overrides."""
    values: dict[str, object] = {
        "name": "Утро",
        "type": RentalTariffType.SLOT,
        "price": "1900.00",
        "slot_start": "09:00",
        "slot_end": "14:00",
        "grace_minutes": 30,
        "deposit_amount": "5000.00",
        "requires_agreement": True,
        "valid_from": TODAY,
    }
    values.update(changes)
    return RentalTariffCreate.model_validate(values)


def test_create_duration_tariff_persists_exact_commercial_terms(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    tariff = RentalTariffService(session).create(rentable_variant.id, duration_data())
    session.commit()

    persisted = session.get(RentalTariffRecord, tariff.id)
    assert persisted is not None
    assert persisted.variant_id == rentable_variant.id
    assert persisted.type is RentalTariffType.DURATION
    assert persisted.duration_minutes == 300
    assert persisted.price == Decimal("2200.00")
    assert persisted.grace_minutes == 30
    assert persisted.deposit_amount == Decimal("5000.00")
    assert persisted.requires_agreement is True


def test_create_slot_tariff_persists_boundaries(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    tariff = RentalTariffService(session).create(rentable_variant.id, slot_data())
    session.commit()

    assert tariff.type is RentalTariffType.SLOT
    assert tariff.slot_start == time(9, 0)
    assert tariff.slot_end == time(14, 0)
    assert tariff.duration_minutes is None


@pytest.mark.parametrize("duration", [None, 0, -1])
def test_duration_requires_positive_minutes(duration: int | None) -> None:
    with pytest.raises(ValidationError, match="positive duration_minutes"):
        duration_data(duration_minutes=duration)


def test_duration_rejects_slot_fields() -> None:
    with pytest.raises(ValidationError, match="does not accept slot fields"):
        duration_data(slot_start="09:00", slot_end="14:00")


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (None, None, "requires slot_start and slot_end"),
        ("14:00", "14:00", "Overnight or zero-length"),
        ("20:00", "09:00", "Overnight or zero-length"),
    ],
)
def test_slot_requires_unambiguous_daytime_boundaries(
    start: str | None, end: str | None, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        slot_data(slot_start=start, slot_end=end)


def test_binary_float_money_is_rejected() -> None:
    with pytest.raises(ValidationError, match="decimal string"):
        duration_data(price=2200.1)


def test_variant_deposit_is_copied_when_tariff_omits_override(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    session.add(
        Price(
            variant_id=rentable_variant.id,
            price_type=PriceType.RENTAL_DEPOSIT,
            amount=Decimal("4800.00"),
            currency="RUB",
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    session.commit()

    tariff = RentalTariffService(session).create(
        rentable_variant.id, duration_data(deposit_amount=None)
    )

    assert tariff.deposit_amount == Decimal("4800.00")


def test_selectable_query_respects_future_and_expired_validity(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    current = service.create(rentable_variant.id, duration_data(name="Текущий"))
    future = service.create(
        rentable_variant.id,
        duration_data(name="Будущий", valid_from=date(2026, 10, 10)),
    )
    expired = service.create(
        rentable_variant.id,
        duration_data(
            name="Истёкший",
            valid_from=date(2026, 9, 1),
            valid_to=date(2026, 10, 8),
        ),
    )
    session.commit()

    selected_ids = {row.id for row in service.list_selectable(rentable_variant.id, on_date=TODAY)}
    assert current.id in selected_ids
    assert future.id not in selected_ids
    assert expired.id not in selected_ids
    assert future.id in {
        row.id
        for row in service.list_selectable(
            rentable_variant.id, on_date=date(2026, 10, 10)
        )
    }


def test_meaningful_edit_always_creates_version_and_preserves_old_row(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    old = service.create(rentable_variant.id, slot_data())
    session.commit()
    old_snapshot = old.to_snapshot()

    new = service.create_version(
        old.id,
        RentalTariffUpdate.model_validate(slot_data(price="2300.00").model_dump()),
        today=TODAY,
    )
    session.commit()

    assert new.id != old.id
    assert new.lineage_id == old.lineage_id
    assert new.version == 2
    assert new.price == Decimal("2300.00")
    assert old.status is RentalTariffStatus.SUPERSEDED
    assert session.get(RentalTariffRecord, old.id).to_snapshot() == old_snapshot  # type: ignore[union-attr]
    assert [row.version for row in service.history(new.id)] == [2, 1]


def test_future_version_does_not_replace_current_before_start(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    old = service.create(rentable_variant.id, slot_data())
    session.commit()
    new = service.create_version(
        old.id,
        RentalTariffUpdate.model_validate(
            slot_data(price="2300.00", valid_from=date(2026, 11, 1)).model_dump()
        ),
        today=TODAY,
    )
    session.commit()

    october = service.list_selectable(rentable_variant.id, on_date=date(2026, 10, 31))
    november = service.list_selectable(rentable_variant.id, on_date=date(2026, 11, 1))
    assert [row.id for row in october] == [old.id]
    assert [row.id for row in november] == [new.id]
    assert old.valid_to == date(2026, 10, 31)
    assert old.status is RentalTariffStatus.ACTIVE


def test_used_and_versioned_tariffs_cannot_be_hard_deleted(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    used = service.create(rentable_variant.id, duration_data(name="Использован"))
    service.mark_used(used.id)
    with pytest.raises(RentalTariffDeleteBlockedError):
        service.hard_delete_unused(used.id)

    old = service.create(rentable_variant.id, duration_data(name="Версионный"))
    service.create_version(
        old.id,
        RentalTariffUpdate.model_validate(duration_data(price="2400.00").model_dump()),
        today=TODAY,
    )
    with pytest.raises(RentalTariffDeleteBlockedError):
        service.hard_delete_unused(old.id)


def test_single_unused_tariff_can_be_hard_deleted(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    tariff = service.create(rentable_variant.id, duration_data())

    service.hard_delete_unused(tariff.id)
    session.commit()

    assert session.get(RentalTariffRecord, tariff.id) is None


def test_tariff_requires_exactly_one_operationally_rentable_variant(session: Session) -> None:
    category = Category(title="Обычные товары", slug="ordinary")
    product = CatalogProduct(title="Товар", slug="item", category=category)
    variant = CatalogVariant(
        product=product,
        title="Обычный",
        sku="ORDINARY-1",
        barcode="2000000000888",
        attributes={},
    )
    session.add_all([category, product, variant])
    session.commit()

    with pytest.raises(RentableVariantNotFoundError):
        RentalTariffService(session).create(variant.id, duration_data())


def test_snapshot_is_deterministic_and_terms_come_from_exact_version(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    tariff = RentalTariffService(session).create(rentable_variant.id, slot_data())
    session.commit()

    first = tariff.to_snapshot()
    second = tariff.to_snapshot()
    assert first == second
    assert first == {
        "tariff_id": str(tariff.id),
        "tariff_version": 1,
        "name": "Утро",
        "type": "slot",
        "price": "1900.00",
        "duration_minutes": None,
        "slot_start": "09:00",
        "slot_end": "14:00",
        "grace_minutes": 30,
        "deposit_amount": "5000.00",
        "requires_agreement": True,
    }
    assert tariff.format_terms() == (
        "09:00–14:00 · 1 900 ₽\n"
        "Льготный период: 30 мин\n"
        "Обеспечительный платёж: 5 000 ₽"
    )
    response = RentalTariffRead.from_record(tariff)
    assert response.snapshot == first
    assert response.tariff_terms == tariff.format_terms()


def test_tariff_archive_removes_it_from_selectable_query(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    service = RentalTariffService(session)
    tariff = service.create(rentable_variant.id, duration_data())
    service.archive(tariff.id)

    assert tariff.status is RentalTariffStatus.ARCHIVED
    assert service.list_selectable(rentable_variant.id, on_date=TODAY) == []


def test_each_tariff_row_has_one_non_nullable_variant_owner(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    tariff = RentalTariffService(session).create(rentable_variant.id, duration_data())
    session.commit()

    row = session.scalar(select(RentalTariffRecord).where(RentalTariffRecord.id == tariff.id))
    assert row is not None
    assert row.variant_id == rentable_variant.id
    assert RentalTariffRecord.__table__.c.variant_id.nullable is False


def test_normal_core_api_supports_create_list_version_and_history(
    session: Session, rentable_variant: CatalogVariant
) -> None:
    """Tariff operations are available through protected first-party routes, not SQLAdmin."""
    user = User(
        email="tariffs@example.com",
        full_name="Tariff Operator",
        password_hash="not-used",
        is_active=True,
        is_admin=True,
    )
    session.add(user)
    session.commit()
    app = create_app()

    def override_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    try:
        created = client.post(
            f"/api/rental/variants/{rentable_variant.id}/tariffs",
            json=slot_data().model_dump(mode="json"),
        )
        assert created.status_code == 201
        assert created.json()["tariff_terms"].startswith("09:00–14:00 · 1 900 ₽")
        tariff_id = created.json()["id"]

        listed = client.get(
            f"/api/rental/variants/{rentable_variant.id}/tariffs",
            params={"on_date": TODAY.isoformat()},
        )
        assert listed.status_code == 200
        assert [row["id"] for row in listed.json()] == [tariff_id]

        changed = client.put(
            f"/api/rental/tariffs/{tariff_id}",
            json=slot_data(price="2300.00").model_dump(mode="json"),
        )
        assert changed.status_code == 200
        assert changed.json()["version"] == 2

        history = client.get(f"/api/rental/tariffs/{changed.json()['id']}/history")
        assert history.status_code == 200
        assert [row["version"] for row in history.json()] == [2, 1]
    finally:
        client.close()
        app.dependency_overrides.clear()

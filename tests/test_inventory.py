from __future__ import annotations

import re
from collections.abc import Generator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.identity.models import User
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.inventory.repository import StockMovementRepository
from core.inventory.service import (
    InventoryService,
    InventoryVariantError,
    MovementSourceRequiredError,
    QuantityDeltaError,
)
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for inventory ledger tests."""
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
            StockMovement.__table__,
        ],
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create an active variant that can be referenced by stock movements."""
    category = Category(title="Cameras", slug="cameras")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Film camera", slug="film-camera", category_id=category.id)
    session.add(product)
    session.flush()
    value = CatalogVariant(product_id=product.id, title="Black body", sku="SKU-000001")
    session.add(value)
    session.commit()
    return value


def build_movement(variant_id: object, quantity_delta: Decimal = Decimal("1")) -> StockMovement:
    """Build a valid immutable receipt movement for test setup."""
    return StockMovement(
        variant_id=variant_id,
        movement_type=MovementType.RECEIPT,
        quantity_delta=quantity_delta,
        source_type=SourceType.RECEIPT,
        source_id=generate_uuid_v7(),
        notes="Initial receipt",
    )


def test_stock_movement_records_decimal_variant_change(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """The ledger stores one UUIDv7 movement for a catalog variant and Decimal delta."""
    movement = build_movement(variant.id, Decimal("2.500"))
    StockMovementRepository(session).add(movement)
    session.commit()

    assert movement.id.version == 7
    assert movement.variant_id == variant.id
    assert movement.quantity_delta == Decimal("2.500")
    assert movement.created_by_id is None


def test_stock_movement_rejects_zero_delta_and_missing_source_id(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Database constraints reject zero changes and source-less ledger records."""
    StockMovementRepository(session).add(build_movement(variant.id, Decimal("0")))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    missing_source = build_movement(variant.id)
    missing_source.source_id = None  # type: ignore[assignment]
    session.add(missing_source)
    with pytest.raises(IntegrityError):
        session.flush()


def test_movement_enums_match_inventory_design() -> None:
    """Movement and source types retain the documented immutable ledger vocabulary."""
    assert [item.value for item in MovementType] == [
        "receipt",
        "sale",
        "return",
        "adjustment",
        "write_off",
        "transfer_in",
        "transfer_out",
        "reversal",
    ]
    assert [item.value for item in SourceType] == ["receipt", "sale", "inventory", "system"]


def test_stock_movement_repository_is_append_and_read_only(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """The repository exposes add and history reads without update or delete operations."""
    repository = StockMovementRepository(session)
    first = build_movement(variant.id, Decimal("2"))
    second = build_movement(variant.id, Decimal("-1"))
    repository.add(first)
    repository.add(second)
    session.commit()

    assert repository.get(first.id) == first
    assert {movement.id for movement in repository.list_for_variant(variant.id)} == {
        first.id,
        second.id,
    }
    assert not hasattr(repository, "update")
    assert not hasattr(repository, "delete")


def test_stock_movement_cannot_be_soft_deleted(variant: CatalogVariant) -> None:
    """Ledger corrections must be compensating records rather than deleted history."""
    movement = build_movement(variant.id)

    with pytest.raises(RuntimeError, match="immutable"):
        movement.soft_delete()
    with pytest.raises(RuntimeError, match="immutable"):
        movement.restore()


def test_stock_movement_migration_revision_identifier_is_short() -> None:
    """Inventory migration revision identifiers remain within the project Alembic limit."""
    migration_path = Path("migrations/versions/0010_stock_movements.py")
    match = re.search(r'^revision: str = "([^"]+)"$', migration_path.read_text(), re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32


def test_inventory_service_creates_attributed_positive_and_negative_movements(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Controlled movement creation normalizes Decimal deltas and records the actor."""
    service = InventoryService(session)
    actor_id = generate_uuid_v7()
    source_id = generate_uuid_v7()
    received = service.create_movement(
        variant.id,
        MovementType.RECEIPT,
        "2.500",
        SourceType.RECEIPT,
        source_id,
        actor_id=actor_id,
    )
    sold = service.create_movement(
        variant.id,
        MovementType.SALE,
        Decimal("-1.250"),
        SourceType.SALE,
        generate_uuid_v7(),
    )

    assert received.quantity_delta == Decimal("2.500")
    assert received.created_by_id == actor_id
    assert sold.quantity_delta == Decimal("-1.250")
    assert sold.created_by_id is None


def test_inventory_service_flushes_without_committing(
    session: Session,
    variant: CatalogVariant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Movement creation flushes its row but leaves transaction commit ownership to callers."""
    service = InventoryService(session)

    def fail_commit() -> None:
        raise AssertionError("InventoryService must not commit.")

    monkeypatch.setattr(session, "commit", fail_commit)
    movement = service.create_movement(
        variant.id,
        MovementType.RECEIPT,
        "1",
        SourceType.RECEIPT,
        generate_uuid_v7(),
    )
    session.expunge(movement)

    assert session.get(StockMovement, movement.id) is not None


def test_inventory_service_reverses_historical_movement_without_variant_validation(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """A compensating entry can be appended after its original variant is archived."""
    service = InventoryService(session)
    original = service.create_movement(
        variant.id,
        MovementType.RECEIPT,
        "2.500",
        SourceType.RECEIPT,
        generate_uuid_v7(),
    )
    variant.soft_delete()
    session.commit()

    reversal = service.reverse_movement(
        original,
        source_type=SourceType.RECEIPT,
        source_id=generate_uuid_v7(),
        actor_id=generate_uuid_v7(),
    )

    assert reversal.movement_type is MovementType.REVERSAL
    assert reversal.variant_id == original.variant_id
    assert reversal.quantity_delta == -original.quantity_delta
    assert reversal.created_by_id is not None


@pytest.mark.parametrize(
    "quantity_delta",
    ["invalid", Decimal("0"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_inventory_service_rejects_invalid_quantity_deltas(
    session: Session,
    variant: CatalogVariant,
    quantity_delta: Decimal | str,
) -> None:
    """Ledger creation rejects invalid, zero, and non-finite quantity delta values."""
    service = InventoryService(session)
    with pytest.raises(QuantityDeltaError, match=str(variant.id)):
        service.create_movement(
            variant.id,
            MovementType.RECEIPT,
            quantity_delta,
            SourceType.RECEIPT,
            generate_uuid_v7(),
        )


def test_inventory_service_rejects_missing_and_archived_variants_with_context(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Ledger creation reports useful context for unavailable variants and missing sources."""
    service = InventoryService(session)
    missing_variant_id = generate_uuid_v7()
    with pytest.raises(InventoryVariantError, match=str(missing_variant_id)):
        service.create_movement(
            missing_variant_id,
            MovementType.RECEIPT,
            "1",
            SourceType.RECEIPT,
            generate_uuid_v7(),
        )
    with pytest.raises(MovementSourceRequiredError, match=str(variant.id)):
        service.create_movement(
            variant.id,
            MovementType.RECEIPT,
            "1",
            SourceType.RECEIPT,
            None,
        )

    variant.soft_delete()
    session.commit()
    with pytest.raises(InventoryVariantError, match=str(variant.id)):
        service.create_movement(
            variant.id,
            MovementType.RECEIPT,
            "1",
            SourceType.RECEIPT,
            generate_uuid_v7(),
        )


def test_inventory_service_aggregates_balances_in_sql(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Balances are Decimal SQL sums and variants without movement history return zero."""
    service = InventoryService(session)
    assert service.get_balance(variant.id) == Decimal("0")

    service.create_movement(
        variant.id,
        MovementType.RECEIPT,
        "5",
        SourceType.RECEIPT,
        generate_uuid_v7(),
    )
    service.create_movement(
        variant.id,
        MovementType.SALE,
        "-1.5",
        SourceType.SALE,
        generate_uuid_v7(),
    )

    assert service.get_balance(variant.id) == Decimal("3.500")
    assert [movement.quantity_delta for movement in service.list_movements(variant.id)] == [
        Decimal("5.000"),
        Decimal("-1.500"),
    ]


def test_inventory_service_aggregates_requested_variant_balances(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """One grouped query returns a balance entry for every requested variant identifier."""
    category = Category(title="Lenses", slug="lenses")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Prime lens", slug="prime-lens", category_id=category.id)
    session.add(product)
    session.flush()
    second_variant = CatalogVariant(product_id=product.id, title="50mm", sku="SKU-000002")
    no_history_variant = CatalogVariant(product_id=product.id, title="85mm", sku="SKU-000003")
    session.add_all([second_variant, no_history_variant])
    session.commit()

    service = InventoryService(session)
    service.create_movement(
        variant.id,
        MovementType.RECEIPT,
        "2",
        SourceType.RECEIPT,
        generate_uuid_v7(),
    )
    service.create_movement(
        second_variant.id,
        MovementType.ADJUSTMENT,
        "-0.5",
        SourceType.INVENTORY,
        generate_uuid_v7(),
    )

    assert service.get_balances([variant.id, second_variant.id, no_history_variant.id]) == {
        variant.id: Decimal("2.000"),
        second_variant.id: Decimal("-0.500"),
        no_history_variant.id: Decimal("0"),
    }

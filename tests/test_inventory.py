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

from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, Category
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.rental.allocation_schemas import InventoryAdjustmentReason
from core.rental.allocation_workflow import (
    InventoryAdjustmentForbiddenError,
    InventoryRentalWorkflow,
    RentalAllocationError,
)
from core.rental.enums import AssetPurpose
from core.rental.models import RentalAssetRecord
from core.rental.service import RentalAssetService
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the complete persistence slice used by the coordinator."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Category.__table__,
            CatalogProduct.__table__,
            CatalogVariant.__table__,
            StockMovement.__table__,
            RentalAssetRecord.__table__,
        ],
    )
    with sessionmaker(bind=engine, autoflush=False)() as value:
        yield value


@pytest.fixture
def variant(session: Session) -> CatalogVariant:
    """Create a variant with five physical units in the immutable ledger."""
    category = Category(title="Props", slug="props")
    session.add(category)
    session.flush()
    product = CatalogProduct(title="Hat", slug="hat", category_id=category.id)
    session.add(product)
    session.flush()
    value = CatalogVariant(
        product_id=product.id,
        title="Red",
        sku="SKU-000001",
        barcode="2000000000015",
    )
    session.add(value)
    session.flush()
    session.add(
        StockMovement(
            variant_id=value.id,
            movement_type=MovementType.RECEIPT,
            quantity_delta=Decimal("5"),
            source_type=SourceType.RECEIPT,
            source_id=generate_uuid_v7(),
        )
    )
    session.commit()
    return value


def test_allocate_inventory_creates_numbered_assets_without_changing_physical_balance(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Allocation changes purpose projection, while ledger remains physical truth."""
    assets = InventoryRentalWorkflow(session).allocate(variant.id, 2, actor_id=generate_uuid_v7())

    assert [asset.asset_number for asset in assets] == ["RENT-000001", "RENT-000002"]
    assert sum(movement.quantity_delta for movement in session.scalars(select(StockMovement))) == 5
    records = session.scalars(select(RentalAssetRecord))
    assert all(record.intake_item_id is None for record in records)


def test_allocate_rejects_more_than_ordinary_inventory(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """The same physical units cannot be allocated twice."""
    workflow = InventoryRentalWorkflow(session)
    workflow.allocate(variant.id, 4, actor_id=generate_uuid_v7())

    with pytest.raises(RentalAllocationError):
        workflow.allocate(variant.id, 2, actor_id=generate_uuid_v7())

    assert len(session.scalars(select(RentalAssetRecord)).all()) == 4


def test_allocate_rolls_back_partially_staged_assets(
    session: Session,
    variant: CatalogVariant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after staging an asset leaves no partial allocation."""
    original = RentalAssetService.create_from_intake

    def fail_after_one(self: RentalAssetService, **kwargs: object) -> None:
        kwargs["quantity"] = 1
        original(self, **kwargs)
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(RentalAssetService, "create_from_intake", fail_after_one)
    with pytest.raises(RuntimeError, match="simulated"):
        InventoryRentalWorkflow(session).allocate(variant.id, 2, actor_id=generate_uuid_v7())

    assert session.scalars(select(RentalAssetRecord)).all() == []


def test_withdraw_keeps_asset_identity_and_returns_unit_to_ordinary_purpose(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Withdrawal uses SALE purpose and never deletes or reuses the RENT identity."""
    workflow = InventoryRentalWorkflow(session)
    asset = workflow.allocate(variant.id, 1, actor_id=generate_uuid_v7())[0]

    workflow.withdraw(asset.id, actor_id=generate_uuid_v7())

    record = session.get(RentalAssetRecord, asset.id)
    assert record is not None
    assert record.asset_number == "RENT-000001"
    assert record.purpose is AssetPurpose.SALE


def test_adjustment_is_append_only_and_requires_administrator(
    session: Session,
    variant: CatalogVariant,
) -> None:
    """Employees are rejected and an administrator appends an attributed delta."""
    workflow = InventoryRentalWorkflow(session)
    with pytest.raises(InventoryAdjustmentForbiddenError):
        workflow.adjust(
            variant.id,
            Decimal("-1"),
            InventoryAdjustmentReason.GIFT,
            "Gifted",
            actor_id=generate_uuid_v7(),
            is_admin=False,
        )

    movement, balance = workflow.adjust(
        variant.id,
        Decimal("-1"),
        InventoryAdjustmentReason.GIFT,
        "Gifted",
        actor_id=generate_uuid_v7(),
        is_admin=True,
    )
    assert movement.movement_type is MovementType.ADJUSTMENT
    assert movement.notes == "gift: Gifted"
    assert balance == Decimal("4")

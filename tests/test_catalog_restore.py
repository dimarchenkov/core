from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcodes import BarcodeSource
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.catalog.service import (
    CatalogProductRestoreSlugConflictError,
    CatalogRestoreService,
    CatalogVariantRestoreBarcodeConflictError,
)
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.integrations.aqsi.enums import PublicationChannel, PublicationStatus
from core.integrations.aqsi.models import Publication
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord
from core.rental.operations_read_service import RentalOperationsReadService
from core.rental.operations_schemas import CatalogMode, CatalogStatus
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the full persistence graph touched by restore invariants."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def operator(session: Session) -> User:
    """Create an authenticated non-admin operator; restore is a normal lifecycle action."""
    user = User(
        email="operator@example.test",
        full_name="Catalog operator",
        password_hash="unused",
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def client(session: Session, operator: User) -> Generator[TestClient]:
    """Expose restore routes with authentication and the test transaction."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: operator
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _catalog_graph(
    session: Session,
    *,
    suffix: str,
    is_test: bool = False,
) -> tuple[Category, CatalogProduct, CatalogVariant]:
    """Create one Product with immutable operational history for restore assertions."""
    category = Category(title=f"Category {suffix}", slug=f"category-{suffix}")
    session.add(category)
    session.flush()
    product = CatalogProduct(
        title=f"Product {suffix}",
        slug=f"product-{suffix}",
        category_id=category.id,
        is_test=is_test,
    )
    session.add(product)
    session.flush()
    variant = CatalogVariant(
        product_id=product.id,
        title=f"Variant {suffix}",
        sku=f"SKU-{suffix.upper()}",
        barcode=f"BARCODE-{suffix.upper()}",
        barcode_source=BarcodeSource.INTERNAL,
    )
    session.add(variant)
    session.flush()
    session.add_all(
        [
            CatalogVariantBarcode(
                variant_id=variant.id,
                value=variant.barcode,
                source=BarcodeSource.INTERNAL,
            ),
            StockMovement(
                variant_id=variant.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("5"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            Price(
                variant_id=variant.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("450"),
                effective_from=datetime.now(UTC),
            ),
            RentalAssetRecord(
                asset_number=f"RENT-{suffix.upper()}",
                variant_id=variant.id,
                purpose=AssetPurpose.RENTAL,
                condition=AssetCondition.NEW,
                availability=RentalAvailability.AVAILABLE,
            ),
            Publication(
                variant_id=variant.id,
                channel=PublicationChannel.AQSI,
                external_id=f"aqsi-{suffix}",
                status=PublicationStatus.PUBLISHED,
                last_requested_payload_hash="a" * 64,
                last_verified_payload_hash="a" * 64,
            ),
        ]
    )
    session.commit()
    return category, product, variant


def test_product_restore_is_idempotent_visible_and_preserves_history(
    client: TestClient,
    session: Session,
) -> None:
    """Restore changes Product lifecycle state only and composes with Catalog status."""
    _, product, variant = _catalog_graph(session, suffix="simple", is_test=True)
    product.soft_delete()
    session.commit()
    before = {
        "stock": session.scalars(
            select(StockMovement).where(StockMovement.variant_id == variant.id)
        ).all(),
        "prices": session.scalars(select(Price).where(Price.variant_id == variant.id)).all(),
        "assets": session.scalars(
            select(RentalAssetRecord).where(RentalAssetRecord.variant_id == variant.id)
        ).all(),
        "publications": session.scalars(
            select(Publication).where(Publication.variant_id == variant.id)
        ).all(),
    }

    response = client.post(f"/api/catalog/products/{product.id}/restore")
    repeated = client.post(f"/api/catalog/products/{product.id}/restore")

    assert response.status_code == 200
    assert repeated.status_code == 200
    session.refresh(product)
    assert product.deleted_at is None
    assert product.is_test is True
    active = RentalOperationsReadService(session).list_products(mode=CatalogMode.ALL)
    archived = RentalOperationsReadService(session).list_products(
        mode=CatalogMode.ALL,
        catalog_status=CatalogStatus.ARCHIVED,
    )
    assert product.id in {row.id for row in active}
    assert product.id not in {row.id for row in archived}
    stock = session.scalars(
        select(StockMovement).where(StockMovement.variant_id == variant.id)
    ).all()
    prices = session.scalars(select(Price).where(Price.variant_id == variant.id)).all()
    assets = session.scalars(
        select(RentalAssetRecord).where(RentalAssetRecord.variant_id == variant.id)
    ).all()
    publications = session.scalars(
        select(Publication).where(Publication.variant_id == variant.id)
    ).all()
    assert stock == before["stock"]
    assert prices == before["prices"]
    assert assets == before["assets"]
    assert publications == before["publications"]
    assert publications[0].status is PublicationStatus.PUBLISHED


def test_product_restore_preserves_independent_variant_archive_state(
    client: TestClient,
    session: Session,
) -> None:
    """Product archive is not cascading, so Product restore must not restore children."""
    _, product, live_variant = _catalog_graph(session, suffix="multi")
    archived_variant = CatalogVariant(
        product_id=product.id,
        title="Historical",
        sku="SKU-MULTI-HISTORICAL",
        barcode="BARCODE-MULTI-HISTORICAL",
    )
    session.add(archived_variant)
    session.flush()
    archived_variant.soft_delete()
    product.soft_delete()
    session.commit()

    assert client.post(f"/api/catalog/products/{product.id}/restore").status_code == 200

    session.refresh(product)
    session.refresh(live_variant)
    session.refresh(archived_variant)
    assert product.deleted_at is None
    assert live_variant.deleted_at is None
    assert archived_variant.deleted_at is not None


def test_variant_restore_preserves_identifiers_and_blocks_archived_parent(
    client: TestClient,
    session: Session,
) -> None:
    """Variant restore is independent but requires an already-active parent Product."""
    _, product, variant = _catalog_graph(session, suffix="variant")
    original_sku = variant.sku
    original_barcode = variant.barcode
    variant.soft_delete()
    session.commit()

    restored = client.post(f"/api/catalog/variants/{variant.id}/restore")

    assert restored.status_code == 200
    session.refresh(variant)
    assert variant.deleted_at is None
    assert variant.sku == original_sku
    assert variant.barcode == original_barcode
    variant.soft_delete()
    product.soft_delete()
    session.commit()

    blocked = client.post(f"/api/catalog/variants/{variant.id}/restore")

    assert blocked.status_code == 409
    assert "сначала восстановите товар" in blocked.json()["detail"]
    session.refresh(variant)
    assert variant.deleted_at is not None


def test_product_restore_blocks_unavailable_category_and_slug_conflict(
    client: TestClient,
    session: Session,
) -> None:
    """Restore never silently reassigns Category or changes the Product slug."""
    category, product, _ = _catalog_graph(session, suffix="category")
    product.soft_delete()
    category.is_active = False
    session.commit()

    category_blocked = client.post(f"/api/catalog/products/{product.id}/restore")

    assert category_blocked.status_code == 409
    assert "Category category" in category_blocked.json()["detail"]
    session.refresh(product)
    assert product.deleted_at is not None
    category.is_active = True
    duplicate = CatalogProduct(
        title="Duplicate",
        slug=product.slug,
        category_id=category.id,
    )
    session.add(duplicate)
    session.commit()

    slug_blocked = client.post(f"/api/catalog/products/{product.id}/restore")

    assert slug_blocked.status_code == 409
    assert "служебный адрес" in slug_blocked.json()["detail"]
    session.refresh(product)
    assert product.deleted_at is not None


def test_variant_barcode_conflict_blocks_restore_without_mutation(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current operational barcode owner blocks restore; no replacement is generated."""
    _, product, archived = _catalog_graph(session, suffix="barcode-old")
    other = CatalogVariant(
        product_id=product.id,
        title="Other",
        sku="SKU-BARCODE-OTHER",
        barcode="BARCODE-OTHER",
    )
    session.add(other)
    session.flush()
    archived.soft_delete()
    session.commit()
    service = CatalogRestoreService(session)
    monkeypatch.setattr(service._variants, "get_active_by_barcode", lambda _value: other)

    with pytest.raises(CatalogVariantRestoreBarcodeConflictError) as error:
        service.restore_variant(archived.id)

    assert error.value.barcode == "BARCODE-BARCODE-OLD"
    assert archived.deleted_at is not None
    assert archived.barcode == "BARCODE-BARCODE-OLD"


def test_product_slug_conflict_is_rechecked_inside_restore_transaction(
    session: Session,
) -> None:
    """The application layer rechecks active slug ownership under a lock."""
    category, archived, _ = _catalog_graph(session, suffix="slug-old")
    archived.soft_delete()
    active = CatalogProduct(
        title="Active",
        slug=archived.slug,
        category_id=category.id,
    )
    session.add(active)
    session.commit()

    with pytest.raises(CatalogProductRestoreSlugConflictError):
        CatalogRestoreService(session).restore_product(archived.id)

    assert archived.deleted_at is not None

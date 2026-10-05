from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.barcodes import BarcodeSource
from core.catalog.deletion import CatalogHardDeleteService
from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.catalog.test_data import CatalogTestDataService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.intake.enums import IntakeItemKind, IntakeSessionStatus
from core.intake.models import IntakeItemDraft, IntakeSession
from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationChannel,
    PublicationOperation,
    PublicationStatus,
)
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.main import create_app
from core.media.enums import ImageLinkEntityType, ImageLinkRole
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import RentalAssetRecord
from core.rental.operations_read_service import RentalOperationsReadService
from core.rental.operations_schemas import CatalogMode, CatalogStatus
from core.shared.db import Base
from core.supplier.models import Supplier


@pytest.fixture
def session() -> Generator[Session]:
    """Provide a complete in-memory schema for cross-domain delete preflights."""
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
def admin(session: Session) -> User:
    """Create the administrator authorized for irreversible Catalog actions."""
    user = User(
        email="admin@example.test",
        full_name="Admin",
        password_hash="unused",
        is_admin=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def client(session: Session, admin: User) -> Generator[TestClient]:
    """Expose Catalog routes with the test database and an administrator."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _catalog_pair(
    session: Session,
    *,
    suffix: str = "one",
    is_test: bool = False,
) -> tuple[CatalogProduct, CatalogVariant]:
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
    return product, variant


def _add_completed_test_workflow(
    session: Session,
    admin: User,
    product: CatalogProduct,
    variant: CatalogVariant,
    *,
    is_test: bool = False,
    extra_variant: CatalogVariant | None = None,
    rental_asset: bool = False,
) -> tuple[IntakeSession, Receipt, IntakeItemDraft]:
    """Create the durable Intake/Receipt/Inventory graph produced by completion."""
    supplier = Supplier(
        name=f"Test supplier {product.slug}",
        code=f"test-supplier-{product.slug}",
    )
    session.add(supplier)
    session.flush()
    receipt = Receipt(
        number=f"TEST-{product.slug}",
        supplier_id=supplier.id,
        receipt_date=date.today(),
        status=ReceiptStatus.POSTED,
        is_test=is_test,
    )
    intake = IntakeSession(
        owner_id=admin.id,
        status=IntakeSessionStatus.COMPLETED,
        completed_at=datetime.now(UTC),
        is_test=is_test,
    )
    session.add_all([receipt, intake])
    session.flush()
    intake.receipt_id = receipt.id
    item = IntakeItemDraft(
        session_id=intake.id,
        kind=IntakeItemKind.EXISTING_VARIANT,
        product_id=product.id,
        variant_id=variant.id,
        quantity=2,
        purchase_price=Decimal("25"),
    )
    session.add(item)
    session.flush()
    session.add_all(
        [
            ReceiptItem(
                receipt_id=receipt.id,
                variant_id=variant.id,
                quantity=2,
                purchase_price=Decimal("25"),
            ),
            StockMovement(
                variant_id=variant.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("2"),
                source_type=SourceType.RECEIPT,
                source_id=receipt.id,
            ),
        ]
    )
    if rental_asset:
        session.add(
            RentalAssetRecord(
                asset_number=f"REN-{product.slug}",
                variant_id=variant.id,
                intake_item_id=item.id,
                purpose=AssetPurpose.RENTAL,
                condition=AssetCondition.GOOD,
                availability=RentalAvailability.AVAILABLE,
            )
        )
    if extra_variant is not None:
        session.add_all(
            [
                IntakeItemDraft(
                    session_id=intake.id,
                    kind=IntakeItemKind.EXISTING_VARIANT,
                    product_id=extra_variant.product_id,
                    variant_id=extra_variant.id,
                    quantity=1,
                    purchase_price=Decimal("10"),
                ),
                ReceiptItem(
                    receipt_id=receipt.id,
                    variant_id=extra_variant.id,
                    quantity=1,
                    purchase_price=Decimal("10"),
                ),
            ]
        )
    session.commit()
    return intake, receipt, item


def _add_safe_dependencies(
    session: Session,
    product: CatalogProduct,
    variant: CatalogVariant,
) -> Image:
    barcode = CatalogVariantBarcode(
        variant_id=variant.id,
        value=variant.barcode,
        source=BarcodeSource.INTERNAL,
    )
    price = Price(
        variant_id=variant.id,
        price_type=PriceType.RETAIL,
        amount=Decimal("100"),
        currency="RUB",
        effective_from=datetime.now(UTC),
    )
    image = Image(
        source_key="catalog/source.jpg",
        original_filename="source.jpg",
        mime_type="image/jpeg",
        size_bytes=100,
        width=10,
        height=10,
        checksum="safe-image",
    )
    session.add_all([barcode, price, image])
    session.flush()
    session.add(
        ImageLink(
            image_id=image.id,
            entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
            entity_id=product.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    publication = Publication(
        variant_id=variant.id,
        channel=PublicationChannel.AQSI,
        external_id="remote-good",
        status=PublicationStatus.PUBLISHED,
        published_at=datetime.now(UTC),
    )
    session.add(publication)
    session.flush()
    session.add(
        PublicationAttempt(
            publication_id=publication.id,
            operation=PublicationOperation.CREATE,
            status=PublicationAttemptStatus.PUBLISHED,
            payload={"name": product.title},
            payload_hash="hash",
            attempt_number=1,
            requested_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
    )
    session.commit()
    return image


def test_safe_product_hard_delete_removes_owned_rows_but_keeps_shared_image(
    client: TestClient,
    session: Session,
) -> None:
    product, variant = _catalog_pair(session)
    image = _add_safe_dependencies(session, product, variant)
    other_product, _ = _catalog_pair(session, suffix="other")
    shared_link = ImageLink(
        image_id=image.id,
        entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
        entity_id=other_product.id,
        role=ImageLinkRole.PRIMARY,
    )
    session.add(shared_link)
    session.commit()
    product_id = product.id
    variant_id = variant.id
    image_id = image.id
    shared_link_id = shared_link.id

    preflight = client.get(f"/api/catalog/products/{product_id}/hard-delete-preflight")

    assert preflight.status_code == 200
    payload = preflight.json()
    assert payload["can_delete"] is True
    assert {item["code"]: item["count"] for item in payload["will_delete"]} == {
        "products": 1,
        "variants": 1,
        "barcodes": 1,
        "prices": 1,
        "image_links": 1,
        "aqsi_publications": 1,
        "aqsi_attempts": 1,
    }
    assert payload["warnings"] == [
        "Товар публиковался в AQSI. Локальное удаление не удалит его с кассы."
    ]

    response = client.delete(f"/api/catalog/products/{product_id}/hard")

    assert response.status_code == 204
    session.expire_all()
    assert session.get(CatalogProduct, product_id) is None
    assert session.get(CatalogVariant, variant_id) is None
    assert session.scalar(select(CatalogVariantBarcode)) is None
    assert session.scalar(select(Price)) is None
    assert session.scalar(select(Publication)) is None
    assert session.scalar(select(PublicationAttempt)) is None
    assert session.get(Image, image_id) is not None
    assert session.get(ImageLink, shared_link_id) is not None


def test_inventory_history_blocks_product_hard_delete(
    client: TestClient,
    session: Session,
) -> None:
    product, variant = _catalog_pair(session)
    session.add(
        StockMovement(
            variant_id=variant.id,
            movement_type=MovementType.ADJUSTMENT,
            quantity_delta=Decimal("1"),
            source_type=SourceType.INVENTORY,
            source_id=product.id,
        )
    )
    session.commit()

    preflight = client.get(f"/api/catalog/products/{product.id}/hard-delete-preflight").json()
    response = client.delete(f"/api/catalog/products/{product.id}/hard")

    assert preflight["can_delete"] is False
    assert {item["code"] for item in preflight["blockers"]} == {"stock_movements"}
    assert response.status_code == 409
    assert session.get(CatalogProduct, product.id) is not None
    assert session.get(CatalogVariant, variant.id) is not None


def test_posted_receipt_blocks_hard_delete_even_without_stock_row(
    client: TestClient,
    session: Session,
) -> None:
    product, variant = _catalog_pair(session)
    supplier = Supplier(name="Supplier", code="supplier")
    session.add(supplier)
    session.flush()
    receipt = Receipt(
        number="R-1",
        supplier_id=supplier.id,
        receipt_date=date.today(),
        status=ReceiptStatus.POSTED,
    )
    session.add(receipt)
    session.flush()
    session.add(
        ReceiptItem(
            receipt_id=receipt.id,
            variant_id=variant.id,
            quantity=1,
            purchase_price=Decimal("50"),
        )
    )
    session.commit()

    preflight = client.get(f"/api/catalog/products/{product.id}/hard-delete-preflight").json()

    assert preflight["can_delete"] is False
    assert {item["code"] for item in preflight["blockers"]} == {"protected_receipts"}


def test_completed_intake_blocks_hard_delete(
    client: TestClient,
    session: Session,
    admin: User,
) -> None:
    product, variant = _catalog_pair(session)
    intake = IntakeSession(
        owner_id=admin.id,
        status=IntakeSessionStatus.COMPLETED,
        completed_at=datetime.now(UTC),
    )
    session.add(intake)
    session.flush()
    session.add(
        IntakeItemDraft(
            session_id=intake.id,
            kind=IntakeItemKind.EXISTING_VARIANT,
            product_id=product.id,
            variant_id=variant.id,
            quantity=1,
            purchase_price=Decimal("25"),
        )
    )
    session.commit()

    preflight = client.get(f"/api/catalog/products/{product.id}/hard-delete-preflight").json()

    assert preflight["can_delete"] is False
    assert {item["code"] for item in preflight["blockers"]} == {"protected_intake"}


def test_draft_intake_and_receipt_lines_are_safe_owned_cleanup(
    client: TestClient,
    session: Session,
    admin: User,
) -> None:
    product, variant = _catalog_pair(session)
    supplier = Supplier(name="Draft supplier", code="draft-supplier")
    intake = IntakeSession(owner_id=admin.id, status=IntakeSessionStatus.DRAFT)
    session.add_all([supplier, intake])
    session.flush()
    intake_item = IntakeItemDraft(
        session_id=intake.id,
        kind=IntakeItemKind.EXISTING_VARIANT,
        product_id=product.id,
        variant_id=variant.id,
    )
    receipt = Receipt(
        number="DRAFT-1",
        supplier_id=supplier.id,
        receipt_date=date.today(),
        status=ReceiptStatus.DRAFT,
    )
    session.add_all([intake_item, receipt])
    session.flush()
    receipt_item = ReceiptItem(
        receipt_id=receipt.id,
        variant_id=variant.id,
        quantity=1,
        purchase_price=Decimal("10"),
    )
    session.add(receipt_item)
    session.commit()
    intake_item_id = intake_item.id
    receipt_item_id = receipt_item.id

    preflight = client.get(f"/api/catalog/products/{product.id}/hard-delete-preflight").json()
    response = client.delete(f"/api/catalog/products/{product.id}/hard")

    assert preflight["can_delete"] is True
    counts = {item["code"]: item["count"] for item in preflight["will_delete"]}
    assert counts["draft_intake_items"] == 1
    assert counts["draft_receipt_items"] == 1
    assert response.status_code == 204
    assert session.get(IntakeItemDraft, intake_item_id) is None
    assert session.get(ReceiptItem, receipt_item_id) is None
    assert session.get(IntakeSession, intake.id) is not None
    assert session.get(Receipt, receipt.id) is not None


def test_rental_asset_blocks_variant_hard_delete(
    client: TestClient,
    session: Session,
) -> None:
    _, variant = _catalog_pair(session)
    session.add(
        RentalAssetRecord(
            asset_number="REN-000001",
            variant_id=variant.id,
            purpose=AssetPurpose.RENTAL,
            condition=AssetCondition.GOOD,
            availability=RentalAvailability.AVAILABLE,
        )
    )
    session.commit()

    response = client.get(f"/api/catalog/variants/{variant.id}/hard-delete-preflight")

    assert response.status_code == 200
    assert response.json()["can_delete"] is False
    assert {item["code"] for item in response.json()["blockers"]} == {"rental_assets"}


def test_variant_hard_delete_keeps_sibling_and_reports_last_variant(
    client: TestClient,
    session: Session,
) -> None:
    product, first = _catalog_pair(session)
    second = CatalogVariant(
        product_id=product.id,
        title="Second",
        sku="SKU-SECOND",
        barcode="BARCODE-SECOND",
        barcode_source=BarcodeSource.INTERNAL,
    )
    session.add(second)
    session.commit()

    first_preflight = client.get(f"/api/catalog/variants/{first.id}/hard-delete-preflight").json()
    response = client.delete(f"/api/catalog/variants/{first.id}/hard")
    last_preflight = client.get(f"/api/catalog/variants/{second.id}/hard-delete-preflight").json()

    assert first_preflight["is_last_variant"] is False
    assert response.status_code == 204
    assert session.get(CatalogProduct, product.id) is not None
    assert session.get(CatalogVariant, first.id) is None
    assert session.get(CatalogVariant, second.id) is not None
    assert last_preflight["is_last_variant"] is True


def test_hard_delete_is_atomic_when_flush_fails(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product, variant = _catalog_pair(session)
    _add_safe_dependencies(session, product, variant)
    real_flush = session.flush

    def fail_flush(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced failure")

    monkeypatch.setattr(session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="forced failure"):
        CatalogHardDeleteService(session).delete_product(product.id)
    session.rollback()
    monkeypatch.setattr(session, "flush", real_flush)

    assert session.get(CatalogProduct, product.id) is not None
    assert session.get(CatalogVariant, variant.id) is not None
    assert session.scalar(select(CatalogVariantBarcode)) is not None


def test_non_admin_cannot_preflight_or_execute_hard_delete(
    client: TestClient,
    session: Session,
) -> None:
    product, _ = _catalog_pair(session)
    app = client.app
    ordinary = User(
        email="operator@example.test",
        full_name="Operator",
        password_hash="unused",
    )
    session.add(ordinary)
    session.commit()
    app.dependency_overrides[get_current_user] = lambda: ordinary

    preflight = client.get(f"/api/catalog/products/{product.id}/hard-delete-preflight")
    deletion = client.delete(f"/api/catalog/products/{product.id}/hard")

    assert preflight.status_code == 403
    assert deletion.status_code == 403
    assert session.get(CatalogProduct, product.id) is not None


def test_repeated_hard_delete_fails_safely_with_not_found(
    client: TestClient,
    session: Session,
) -> None:
    product, _ = _catalog_pair(session)
    session.commit()

    assert client.delete(f"/api/catalog/products/{product.id}/hard").status_code == 204
    assert client.delete(f"/api/catalog/products/{product.id}/hard").status_code == 404


def test_completed_production_intake_still_blocks_hard_delete_but_allows_archive(
    client: TestClient,
    session: Session,
    admin: User,
) -> None:
    product, variant = _catalog_pair(session, suffix="production-archive")
    _add_completed_test_workflow(session, admin, product, variant)

    assert client.delete(f"/api/catalog/products/{product.id}/hard").status_code == 409
    assert client.delete(f"/api/catalog/products/{product.id}").status_code == 204

    session.expire_all()
    assert session.get(CatalogProduct, product.id).deleted_at is not None


def test_legacy_graph_requires_explicit_classification_then_purges_atomically(
    client: TestClient,
    session: Session,
    admin: User,
) -> None:
    product, variant = _catalog_pair(session, suffix="legacy-test")
    intake, receipt, intake_item = _add_completed_test_workflow(
        session,
        admin,
        product,
        variant,
        rental_asset=True,
    )
    _add_safe_dependencies(session, product, variant)
    product_id = product.id
    variant_id = variant.id
    intake_id = intake.id
    receipt_id = receipt.id
    intake_item_id = intake_item.id

    initial = client.get(f"/api/catalog/products/{product_id}/test-data-preflight")

    assert initial.status_code == 200
    assert initial.json()["is_test"] is False
    assert initial.json()["can_classify"] is True
    assert initial.json()["can_purge"] is False
    assert (
        client.post(
            f"/api/catalog/products/{product_id}/purge-test-data",
            json={"confirm": True},
        ).status_code
        == 409
    )
    assert client.post(f"/api/catalog/products/{product_id}/classify-test-data").status_code == 422
    assert client.post(
        f"/api/catalog/products/{product_id}/classify-test-data",
        json={"confirm": False},
    ).status_code == 422

    classified = client.post(
        f"/api/catalog/products/{product_id}/classify-test-data",
        json={"confirm": True},
    )
    preflight = client.get(f"/api/catalog/products/{product_id}/test-data-preflight").json()

    assert classified.status_code == 204
    assert preflight["is_test"] is True
    assert preflight["can_purge"] is True
    counts = {item["code"]: item["count"] for item in preflight["dependencies"]}
    assert counts["intakes"] == 1
    assert counts["intake_items"] == 1
    assert counts["receipts"] == 1
    assert counts["receipt_items"] == 1
    assert counts["stock_movements"] == 1
    assert counts["rental_assets"] == 1
    assert counts["aqsi_publications"] == 1
    assert any("не будет удалён с кассы AQSI" in warning for warning in preflight["warnings"])

    purged = client.post(
        f"/api/catalog/products/{product_id}/purge-test-data",
        json={"confirm": True},
    )

    assert purged.status_code == 204
    session.expire_all()
    assert session.get(CatalogProduct, product_id) is None
    assert session.get(CatalogVariant, variant_id) is None
    assert session.get(IntakeSession, intake_id) is None
    assert session.get(IntakeItemDraft, intake_item_id) is None
    assert session.get(Receipt, receipt_id) is None
    service = RentalOperationsReadService(session)
    assert all(
        row.id != product_id
        for row in service.list_products(
            mode=CatalogMode.ALL,
            catalog_status=CatalogStatus.ALL,
        )
    )
    assert all(
        row.id != product_id
        for row in service.list_products(
            mode=CatalogMode.ALL,
            catalog_status=CatalogStatus.ARCHIVED,
        )
    )


def test_mixed_test_and_production_graph_blocks_classification_and_purge(
    client: TestClient,
    session: Session,
    admin: User,
) -> None:
    product, variant = _catalog_pair(session, suffix="mixed-test", is_test=True)
    other_product, other_variant = _catalog_pair(session, suffix="mixed-production")
    _add_completed_test_workflow(
        session,
        admin,
        product,
        variant,
        is_test=True,
        extra_variant=other_variant,
    )

    preflight = client.get(f"/api/catalog/products/{product.id}/test-data-preflight").json()
    response = client.post(
        f"/api/catalog/products/{product.id}/purge-test-data",
        json={"confirm": True},
    )

    assert preflight["can_purge"] is False
    assert {item["code"] for item in preflight["blockers"]} == {
        "mixed_intakes",
        "mixed_receipts",
    }
    assert response.status_code == 409
    assert session.get(CatalogProduct, product.id) is not None
    assert session.get(CatalogProduct, other_product.id) is not None


def test_purge_revalidates_and_blocks_new_sale_or_unattributed_inventory_fact(
    client: TestClient,
    session: Session,
) -> None:
    product, variant = _catalog_pair(session, suffix="revalidate", is_test=True)
    session.commit()
    assert client.get(
        f"/api/catalog/products/{product.id}/test-data-preflight"
    ).json()["can_purge"] is True
    session.add(
        StockMovement(
            variant_id=variant.id,
            movement_type=MovementType.SALE,
            quantity_delta=Decimal("-1"),
            source_type=SourceType.SALE,
            source_id=product.id,
        )
    )
    session.commit()

    response = client.post(
        f"/api/catalog/products/{product.id}/purge-test-data",
        json={"confirm": True},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["blockers"] == [
        {
            "code": "unattributed_stock",
            "label": "складское движение вне TEST-прихода",
            "count": 1,
        }
    ]
    assert session.get(CatalogProduct, product.id) is not None


def test_test_data_purge_rolls_back_every_delete_when_flush_fails(
    session: Session,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product, variant = _catalog_pair(session, suffix="atomic", is_test=True)
    intake, receipt, _ = _add_completed_test_workflow(
        session,
        admin,
        product,
        variant,
        is_test=True,
    )
    real_flush = session.flush

    def fail_flush(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("forced test purge failure")

    monkeypatch.setattr(session, "flush", fail_flush)
    with pytest.raises(RuntimeError, match="forced test purge failure"):
        CatalogTestDataService(session).purge(product.id)
    session.rollback()
    monkeypatch.setattr(session, "flush", real_flush)

    assert session.get(CatalogProduct, product.id) is not None
    assert session.get(CatalogVariant, variant.id) is not None
    assert session.get(IntakeSession, intake.id) is not None
    assert session.get(Receipt, receipt.id) is not None


def test_non_admin_cannot_classify_or_purge_test_data(
    client: TestClient,
    session: Session,
) -> None:
    product, _ = _catalog_pair(session, suffix="test-auth")
    ordinary = User(
        email="test-operator@example.test",
        full_name="Test operator",
        password_hash="unused",
    )
    session.add(ordinary)
    session.commit()
    client.app.dependency_overrides[get_current_user] = lambda: ordinary

    assert client.get(
        f"/api/catalog/products/{product.id}/test-data-preflight"
    ).status_code == 403
    assert client.post(
        f"/api/catalog/products/{product.id}/classify-test-data",
        json={"confirm": True},
    ).status_code == 403
    assert client.post(
        f"/api/catalog/products/{product.id}/purge-test-data",
        json={"confirm": True},
    ).status_code == 403
    assert session.get(CatalogProduct, product.id).is_test is False

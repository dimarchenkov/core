from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.customers.models import CustomerRecord
from core.identity.models import User
from core.intake.models import IntakeItemDraft, IntakeSession
from core.integrations.aqsi.enums import PublicationChannel, PublicationStatus
from core.integrations.aqsi.models import Publication
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.media.models import Image, ImageLink
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.rental.enums import AssetCondition, AssetPurpose, RentalAvailability
from core.rental.models import (
    RentalAssetRecord,
    RentalMaintenanceRecord,
    RentalOrderItemRecord,
    RentalOrderRecord,
)
from core.rental.operations_read_service import RentalOperationsReadService
from core.rental.operations_schemas import (
    CatalogAttentionFilter,
    CatalogMode,
    CatalogOperationsSort,
)
from core.shared.db import Base, generate_uuid_v7
from core.supplier.models import Supplier


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the persistence slice read by the Catalog shell projection."""
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
            Image.__table__,
            ImageLink.__table__,
            Price.__table__,
            StockMovement.__table__,
            RentalAssetRecord.__table__,
            RentalOrderRecord.__table__,
            RentalOrderItemRecord.__table__,
            RentalMaintenanceRecord.__table__,
            Supplier.__table__,
            Receipt.__table__,
            ReceiptItem.__table__,
            IntakeSession.__table__,
            IntakeItemDraft.__table__,
            Publication.__table__,
        ],
    )
    with sessionmaker(bind=engine, autoflush=False)() as value:
        yield value


@pytest.fixture
def catalog_data(session: Session) -> dict[str, object]:
    """Create sale, rental, inactive, category, and supplier examples."""
    root = Category(title="Канцелярия", slug="stationery", sort_order=1)
    child = Category(title="Ручки", slug="pens", parent=root, sort_order=1)
    other = Category(title="Архив", slug="archive", sort_order=2)
    supplier = Supplier(name="ООО Поставщик", display_name="Поставщик", code="SUP-000001")
    session.add_all([root, child, other, supplier])
    session.flush()

    sale_product = CatalogProduct(title="Ручка", slug="pen", category_id=child.id)
    rental_product = CatalogProduct(title="Набор маркеров", slug="markers", category_id=child.id)
    inactive_product = CatalogProduct(
        title="Старая ручка",
        slug="old-pen",
        category_id=other.id,
        is_active=False,
    )
    session.add_all([sale_product, rental_product, inactive_product])
    session.flush()
    sale_variant = CatalogVariant(
        product_id=sale_product.id,
        title="Синяя",
        sku="SKU-SALE",
        barcode="4600000000001",
    )
    rental_variant = CatalogVariant(
        product_id=rental_product.id,
        title="Основной",
        sku="SKU-RENT",
        barcode="4600000000002",
    )
    inactive_variant = CatalogVariant(
        product_id=inactive_product.id,
        title="Основной",
        sku="SKU-OLD",
        barcode="4600000000003",
        is_active=False,
    )
    session.add_all([sale_variant, rental_variant, inactive_variant])
    session.flush()
    session.add_all(
        [
            StockMovement(
                variant_id=sale_variant.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("5"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=rental_variant.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("1"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            Price(
                variant_id=sale_variant.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("150"),
                effective_from=datetime.now(UTC),
            ),
            RentalAssetRecord(
                asset_number="RENT-000001",
                variant_id=rental_variant.id,
                purpose=AssetPurpose.RENTAL,
                condition=AssetCondition.NEW,
                availability=RentalAvailability.AVAILABLE,
            ),
            Publication(
                variant_id=sale_variant.id,
                channel=PublicationChannel.AQSI,
                external_id="sale-good",
                status=PublicationStatus.PUBLISHED,
                last_requested_payload_hash="a" * 64,
                last_verified_payload_hash="a" * 64,
            ),
        ]
    )
    receipt = Receipt(
        number="REC-000001",
        supplier_id=supplier.id,
        receipt_date=date.today(),
        status=ReceiptStatus.POSTED,
    )
    session.add(receipt)
    session.flush()
    session.add(
        ReceiptItem(
            receipt_id=receipt.id,
            variant_id=sale_variant.id,
            quantity=5,
            purchase_price=Decimal("50"),
        )
    )
    session.commit()
    return {
        "root": root,
        "supplier": supplier,
        "sale_product": sale_product,
        "rental_product": rental_product,
        "inactive_product": inactive_product,
    }


def _titles(rows: list[object]) -> list[str]:
    return [row.title for row in rows]


def test_catalog_modes_default_to_sale_and_keep_one_shared_catalog(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)

    assert _titles(service.list_products()) == ["Набор маркеров", "Ручка"]
    assert _titles(service.list_products(mode=CatalogMode.RENTAL)) == ["Набор маркеров"]
    assert _titles(service.list_products(mode=CatalogMode.ALL)) == [
        "Набор маркеров",
        "Ручка",
        "Старая ручка",
    ]


def test_parent_category_includes_descendants_and_invalid_category_is_safe(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)
    root = catalog_data["root"]

    assert _titles(service.list_products(category_id=root.id)) == ["Набор маркеров", "Ручка"]
    assert service.list_products(category_id=generate_uuid_v7()) == []


def test_supplier_filter_uses_posted_receipt_history_and_coexists_with_search_and_sort(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)
    supplier = catalog_data["supplier"]

    rows = service.list_products(
        "SKU-SALE",
        supplier_id=supplier.id,
        sort=CatalogOperationsSort.TITLE,
    )

    assert _titles(rows) == ["Ручка"]
    assert service.list_products(supplier_id=generate_uuid_v7()) == []


def test_attention_filters_are_server_backed_and_can_be_combined(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)

    assert _titles(
        service.list_products(attention_filters=frozenset({CatalogAttentionFilter.MISSING_PRICE}))
    ) == ["Набор маркеров"]
    assert _titles(
        service.list_products(attention_filters=frozenset({CatalogAttentionFilter.OUT_OF_STOCK}))
    ) == ["Набор маркеров"]
    assert _titles(
        service.list_products(attention_filters=frozenset({CatalogAttentionFilter.AQSI_PROBLEM}))
    ) == ["Набор маркеров"]
    assert _titles(
        service.list_products(
            attention_filters=frozenset(
                {
                    CatalogAttentionFilter.MISSING_PRICE,
                    CatalogAttentionFilter.MISSING_PHOTO,
                    CatalogAttentionFilter.OUT_OF_STOCK,
                }
            )
        )
    ) == ["Набор маркеров"]

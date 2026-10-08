from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.models import CatalogProduct, CatalogVariant, CatalogVariantBarcode, Category
from core.catalog.schemas import CategoryUpdate
from core.catalog.service import CategoryService
from core.customers.models import CustomerRecord
from core.identity.models import User
from core.intake.models import IntakeItemDraft, IntakeSession
from core.integrations.aqsi.enums import PublicationChannel, PublicationStatus
from core.integrations.aqsi.models import Publication
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.media.enums import ImageLinkEntityType, ImageLinkRole
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
    CatalogStatus,
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


def test_catalog_archive_status_defaults_to_active_and_composes_with_mode(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    archived_product = CatalogProduct(
        title="Архивный проектор",
        slug="archived-projector",
        category_id=category_id,
    )
    session.add(archived_product)
    session.flush()
    variant = CatalogVariant(
        product_id=archived_product.id,
        title="Основной",
        sku="SKU-ARCHIVED-PRODUCT",
        barcode="4600000000091",
    )
    session.add(variant)
    session.flush()
    session.add(
        RentalAssetRecord(
            asset_number="RENT-ARCHIVED-001",
            variant_id=variant.id,
            purpose=AssetPurpose.RENTAL,
            condition=AssetCondition.NEW,
            availability=RentalAvailability.AVAILABLE,
        )
    )
    archived_product.soft_delete()
    session.commit()

    service = RentalOperationsReadService(session)

    assert "Архивный проектор" not in _titles(service.list_products(mode=CatalogMode.ALL))
    archived = service.list_products(
        mode=CatalogMode.ALL,
        catalog_status=CatalogStatus.ARCHIVED,
    )
    archived_row = next(row for row in archived if row.id == archived_product.id)
    assert archived_row.is_archived is True
    assert archived_row.card_variants[0].is_archived is False
    assert _titles(
        service.list_products(
            mode=CatalogMode.RENTAL,
            catalog_status=CatalogStatus.ARCHIVED,
        )
    ) == ["Архивный проектор"]
    assert "Архивный проектор" in _titles(
        service.list_products(mode=CatalogMode.ALL, catalog_status=CatalogStatus.ALL)
    )
    assert "Ручка" in _titles(
        service.list_products(mode=CatalogMode.ALL, catalog_status=CatalogStatus.ALL)
    )


def test_archived_variants_are_separated_from_live_variants_by_status(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    product = CatalogProduct(title="Объектив", slug="lens", category_id=category_id)
    session.add(product)
    session.flush()
    live_variant = CatalogVariant(
        product_id=product.id,
        title="Новый",
        sku="SKU-LENS-LIVE",
        barcode="4600000000092",
    )
    archived_variant = CatalogVariant(
        product_id=product.id,
        title="Старый",
        sku="SKU-LENS-ARCHIVED",
        barcode="4600000000093",
    )
    session.add_all([live_variant, archived_variant])
    session.flush()
    session.add(
        RentalAssetRecord(
            asset_number="RENT-ARCHIVED-VARIANT-001",
            variant_id=archived_variant.id,
            purpose=AssetPurpose.RENTAL,
            condition=AssetCondition.NEW,
            availability=RentalAvailability.AVAILABLE,
        )
    )
    archived_variant.soft_delete()
    session.commit()

    service = RentalOperationsReadService(session)
    active_row = next(
        row for row in service.list_products(mode=CatalogMode.ALL) if row.id == product.id
    )
    archived_row = next(
        row
        for row in service.list_products(
            mode=CatalogMode.ALL,
            catalog_status=CatalogStatus.ARCHIVED,
        )
        if row.id == product.id
    )
    all_row = next(
        row
        for row in service.list_products(
            mode=CatalogMode.ALL,
            catalog_status=CatalogStatus.ALL,
        )
        if row.id == product.id
    )

    assert [variant.sku for variant in active_row.card_variants] == ["SKU-LENS-LIVE"]
    assert [variant.sku for variant in archived_row.card_variants] == [
        "SKU-LENS-ARCHIVED"
    ]
    assert archived_row.card_variants[0].is_archived is True
    assert {variant.sku for variant in all_row.card_variants} == {
        "SKU-LENS-LIVE",
        "SKU-LENS-ARCHIVED",
    }
    assert _titles(
        service.list_products(
            mode=CatalogMode.RENTAL,
            catalog_status=CatalogStatus.ARCHIVED,
        )
    ) == ["Объектив"]
    assert service.list_products("SKU-LENS-ARCHIVED", mode=CatalogMode.ALL) == []
    assert _titles(
        service.list_products(
            "SKU-LENS-ARCHIVED",
            mode=CatalogMode.ALL,
            catalog_status=CatalogStatus.ARCHIVED,
        )
    ) == ["Объектив"]


def test_parent_category_includes_descendants_and_invalid_category_is_safe(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)
    root = catalog_data["root"]

    assert _titles(service.list_products(category_id=root.id)) == ["Набор маркеров", "Ручка"]
    assert service.list_products(category_id=generate_uuid_v7()) == []


def test_catalog_category_filter_survives_category_rename_and_move(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    """Catalog filtering follows the current Category tree after dictionary edits."""
    old_root = catalog_data["root"]
    sale_product = catalog_data["sale_product"]
    child = session.get(Category, sale_product.category_id)
    new_root = Category(title="Творчество", slug="creative")
    session.add(new_root)
    session.flush()

    CategoryService(session).update_category(
        child.id,
        CategoryUpdate(title="Гелевые ручки", parent_id=new_root.id),
    )
    session.commit()

    catalog = RentalOperationsReadService(session)
    assert catalog.list_products(category_id=old_root.id) == []
    assert _titles(catalog.list_products(category_id=new_root.id)) == [
        "Набор маркеров",
        "Ручка",
    ]


def test_supplier_filter_uses_posted_receipt_history_and_coexists_with_search_and_sort(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)
    supplier = catalog_data["supplier"]

    rows = service.list_products(
        "SKU-SALE",
        mode=CatalogMode.SALE,
        catalog_status=CatalogStatus.ACTIVE,
        category_id=catalog_data["root"].id,
        supplier_id=supplier.id,
        sort=CatalogOperationsSort.STOCK_DESC,
    )

    assert _titles(rows) == ["Ручка"]
    assert service.list_products(supplier_id=generate_uuid_v7()) == []


def test_general_catalog_sorts_use_real_product_creation_time_and_title(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    now = datetime.now(UTC)
    old_product = CatalogProduct(
        title="Янтарь",
        slug="sort-old",
        category_id=category_id,
        created_at=now - timedelta(days=2),
    )
    new_product = CatalogProduct(
        title="Альфа",
        slug="sort-new",
        category_id=category_id,
        created_at=now - timedelta(days=1),
    )
    session.add_all([old_product, new_product])
    session.flush()
    session.add_all([
        CatalogVariant(
            product_id=old_product.id,
            title="Основной",
            sku="SKU-SORT-OLD",
            barcode="4600000000101",
        ),
        CatalogVariant(
            product_id=new_product.id,
            title="Основной",
            sku="SKU-SORT-NEW",
            barcode="4600000000102",
        ),
    ])
    session.commit()
    service = RentalOperationsReadService(session)

    newest = _titles(service.list_products(mode=CatalogMode.SALE))
    oldest = _titles(service.list_products(
        mode=CatalogMode.SALE,
        sort=CatalogOperationsSort.OLDEST,
    ))
    alphabetical = _titles(service.list_products(
        mode=CatalogMode.ALL,
        sort=CatalogOperationsSort.TITLE,
    ))

    assert newest.index("Альфа") < newest.index("Янтарь")
    assert oldest.index("Янтарь") < oldest.index("Альфа")
    assert alphabetical == sorted(alphabetical, key=str.casefold)


def test_product_price_sort_uses_minimum_current_variant_price_and_missing_last(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    products = [
        CatalogProduct(title="Несколько цен", slug="multi-price", category_id=category_id),
        CatalogProduct(title="Средняя цена", slug="middle-price", category_id=category_id),
        CatalogProduct(title="Без цены", slug="missing-price", category_id=category_id),
    ]
    session.add_all(products)
    session.flush()
    variants = [
        CatalogVariant(
            product_id=products[0].id,
            title="A",
            sku="SKU-PRICE-A",
            barcode="4600000000111",
        ),
        CatalogVariant(
            product_id=products[0].id,
            title="B",
            sku="SKU-PRICE-B",
            barcode="4600000000112",
        ),
        CatalogVariant(
            product_id=products[1].id,
            title="A",
            sku="SKU-PRICE-M",
            barcode="4600000000113",
        ),
        CatalogVariant(
            product_id=products[2].id,
            title="A",
            sku="SKU-PRICE-X",
            barcode="4600000000114",
        ),
    ]
    session.add_all(variants)
    session.flush()
    session.add_all(
        [
            Price(
                variant_id=variants[0].id,
                price_type=PriceType.RETAIL,
                amount=Decimal("900"),
                effective_from=datetime.now(UTC),
            ),
            Price(
                variant_id=variants[1].id,
                price_type=PriceType.RETAIL,
                amount=Decimal("390"),
                effective_from=datetime.now(UTC),
            ),
            Price(
                variant_id=variants[2].id,
                price_type=PriceType.RETAIL,
                amount=Decimal("400"),
                effective_from=datetime.now(UTC),
            ),
        ]
    )
    session.commit()
    service = RentalOperationsReadService(session)

    ascending = _titles(service.list_products(sort=CatalogOperationsSort.PRICE_ASC))
    descending = _titles(service.list_products(sort=CatalogOperationsSort.PRICE_DESC))

    assert ascending.index("Несколько цен") < ascending.index("Средняя цена")
    assert descending.index("Средняя цена") < descending.index("Несколько цен")
    assert ascending.index("Без цены") > ascending.index("Средняя цена")
    assert descending.index("Без цены") > descending.index("Несколько цен")


def test_product_stock_sort_sums_sale_quantities_and_supports_negative_balances(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    negative = CatalogProduct(title="Минус", slug="negative-stock", category_id=category_id)
    combined = CatalogProduct(title="Сумма", slug="combined-stock", category_id=category_id)
    session.add_all([negative, combined])
    session.flush()
    variants = [
        CatalogVariant(
            product_id=negative.id,
            title="A",
            sku="SKU-STOCK-N",
            barcode="4600000000121",
        ),
        CatalogVariant(
            product_id=combined.id,
            title="A",
            sku="SKU-STOCK-A",
            barcode="4600000000122",
        ),
        CatalogVariant(
            product_id=combined.id,
            title="B",
            sku="SKU-STOCK-B",
            barcode="4600000000123",
        ),
    ]
    session.add_all(variants)
    session.flush()
    session.add_all(
        [
            StockMovement(
                variant_id=variants[0].id,
                movement_type=MovementType.ADJUSTMENT,
                quantity_delta=Decimal("-2"),
                source_type=SourceType.INVENTORY,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=variants[1].id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("2"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=variants[2].id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("3"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
        ]
    )
    session.commit()
    service = RentalOperationsReadService(session)

    ascending = service.list_products(sort=CatalogOperationsSort.STOCK_ASC)
    descending = service.list_products(sort=CatalogOperationsSort.STOCK_DESC)
    ascending_titles = _titles(ascending)
    descending_titles = _titles(descending)
    combined_row = next(row for row in descending if row.id == combined.id)
    negative_row = next(row for row in ascending if row.id == negative.id)

    assert ascending_titles.index("Минус") < ascending_titles.index("Сумма")
    assert descending_titles.index("Сумма") < descending_titles.index("Минус")
    assert service._product_sale_sort_stock(combined_row) == Decimal("5")
    assert service._product_sale_sort_stock(negative_row) == Decimal("-2")


def test_incompatible_sort_falls_back_to_each_mode_default(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)

    sale_fallback = service.list_products(
        mode=CatalogMode.SALE,
        sort=CatalogOperationsSort.PROFIT,
    )
    sale_default = service.list_products(mode=CatalogMode.SALE)
    rental_fallback = service.list_products(
        mode=CatalogMode.RENTAL,
        sort=CatalogOperationsSort.NEWEST,
    )
    rental_default = service.list_products(mode=CatalogMode.RENTAL)

    assert [row.id for row in sale_fallback] == [row.id for row in sale_default]
    assert [row.id for row in rental_fallback] == [row.id for row in rental_default]


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


def test_product_cards_expose_current_commercial_facts_without_technical_names(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    service = RentalOperationsReadService(session)

    rows = service.list_products(mode=CatalogMode.ALL)
    sale = next(row for row in rows if row.title == "Ручка")
    rental = next(row for row in rows if row.title == "Набор маркеров")

    assert sale.category_label == "Канцелярия › Ручки"
    assert sale.variant_count == 1
    assert sale.rental_economics_applicable is False
    assert sale.card_variants[0].title == "Синяя"
    assert sale.card_variants[0].sku == "SKU-SALE"
    assert sale.card_variants[0].current_retail_price == Decimal("150")
    assert sale.card_variants[0].stock_balance == Decimal("5")
    assert sale.card_variants[0].sale_quantity == Decimal("5")
    assert sale.card_variants[0].rental_asset_count == 0
    assert sale.card_variants[0].sale_row_visible is True
    assert sale.card_variants[0].rental_row_visible is False
    assert sale.card_variants[0].aqsi_status is PublicationStatus.PUBLISHED
    assert sale.card_variants[0].aqsi_is_current is True

    assert rental.card_variants[0].title is None
    assert rental.rental_economics_applicable is True
    assert rental.economics.profit == Decimal("0")
    assert rental.card_variants[0].current_retail_price is None
    assert rental.card_variants[0].current_retail_price != Decimal("0")
    assert rental.card_variants[0].current_rental_price is None
    assert rental.card_variants[0].current_rental_price != Decimal("0")
    assert rental.card_variants[0].stock_balance == Decimal("1")
    assert rental.card_variants[0].sale_quantity == Decimal("0")
    assert rental.card_variants[0].rental_asset_count == 1
    assert rental.card_variants[0].sale_row_visible is False
    assert rental.card_variants[0].rental_row_visible is True


def test_product_image_prefers_own_primary_and_uses_only_unambiguous_variant_fallback(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    """Product cards never pick an arbitrary image when several Variants have media."""
    product = catalog_data["sale_product"]
    first_variant = session.scalar(select(CatalogVariant).where(CatalogVariant.sku == "SKU-SALE"))
    assert first_variant is not None

    def image(key: str) -> Image:
        return Image(
            source_key=f"{key}.jpg",
            original_filename=f"{key}.jpg",
            mime_type="image/jpeg",
            size_bytes=100,
            width=10,
            height=10,
            checksum=key,
        )

    variant_image = image("variant-primary")
    session.add(variant_image)
    session.flush()
    session.add(
        ImageLink(
            image_id=variant_image.id,
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=first_variant.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    session.commit()

    service = RentalOperationsReadService(session)
    row = next(item for item in service.list_products() if item.id == product.id)
    assert row.primary_image_id == variant_image.id

    product_image = image("product-primary")
    session.add(product_image)
    session.flush()
    session.add(
        ImageLink(
            image_id=product_image.id,
            entity_type=ImageLinkEntityType.CATALOG_PRODUCT,
            entity_id=product.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    session.commit()
    row = next(item for item in service.list_products() if item.id == product.id)
    assert row.primary_image_id == product_image.id

    session.query(ImageLink).filter(
        ImageLink.entity_type == ImageLinkEntityType.CATALOG_PRODUCT,
        ImageLink.entity_id == product.id,
    ).delete()
    second_variant = CatalogVariant(
        product_id=product.id,
        title="Красная",
        sku="SKU-SALE-RED",
        barcode="4600000000099",
    )
    second_image = image("second-variant-primary")
    session.add_all([second_variant, second_image])
    session.flush()
    session.add(
        ImageLink(
            image_id=second_image.id,
            entity_type=ImageLinkEntityType.CATALOG_VARIANT,
            entity_id=second_variant.id,
            role=ImageLinkRole.PRIMARY,
        )
    )
    session.commit()

    row = next(item for item in service.list_products() if item.id == product.id)
    assert row.primary_image_id is None


def test_product_cards_include_all_active_variants_with_zero_and_negative_stock(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    category_id = catalog_data["sale_product"].category_id
    product = CatalogProduct(title="Футболка", slug="shirt", category_id=category_id)
    session.add(product)
    session.flush()
    beige = CatalogVariant(
        product_id=product.id,
        title="Бежевая",
        sku="SKU-BEIGE",
        barcode="4600000000011",
    )
    gray = CatalogVariant(
        product_id=product.id,
        title="Серая",
        sku="SKU-GRAY",
        barcode="4600000000012",
    )
    black = CatalogVariant(
        product_id=product.id,
        title="Чёрная",
        sku="SKU-BLACK",
        barcode="4600000000013",
    )
    archived = CatalogVariant(
        product_id=product.id,
        title="Архивная",
        sku="SKU-ARCHIVED",
        barcode="4600000000014",
        is_active=False,
    )
    session.add_all([beige, gray, black, archived])
    session.flush()
    session.add_all(
        [
            Price(
                variant_id=beige.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("390"),
                effective_from=datetime.now(UTC),
            ),
            Price(
                variant_id=beige.id,
                price_type=PriceType.RENTAL,
                amount=Decimal("190"),
                effective_from=datetime.now(UTC),
            ),
            Price(
                variant_id=black.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("420"),
                effective_from=datetime.now(UTC),
            ),
            StockMovement(
                variant_id=beige.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("12"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=black.id,
                movement_type=MovementType.ADJUSTMENT,
                quantity_delta=Decimal("-2"),
                source_type=SourceType.INVENTORY,
                source_id=generate_uuid_v7(),
            ),
            RentalAssetRecord(
                asset_number="RENT-MIXED-001",
                variant_id=beige.id,
                purpose=AssetPurpose.RENTAL,
                condition=AssetCondition.NEW,
                availability=RentalAvailability.AVAILABLE,
            ),
            Publication(
                variant_id=beige.id,
                channel=PublicationChannel.AQSI,
                external_id="beige-good",
                status=PublicationStatus.PUBLISHED,
                last_requested_payload_hash="b" * 64,
                last_verified_payload_hash="a" * 64,
            ),
            Publication(
                variant_id=black.id,
                channel=PublicationChannel.AQSI,
                external_id="black-good",
                status=PublicationStatus.FAILED,
                last_error="AQSI rejected the payload",
            ),
        ]
    )
    session.commit()

    row = next(
        item
        for item in RentalOperationsReadService(session).list_products(mode=CatalogMode.ALL)
        if item.id == product.id
    )
    variants = {variant.sku: variant for variant in row.card_variants}

    assert list(variants) == ["SKU-BEIGE", "SKU-GRAY", "SKU-BLACK"]
    assert row.variant_count == 3
    assert variants["SKU-BEIGE"].current_retail_price == Decimal("390")
    assert variants["SKU-BEIGE"].current_rental_price == Decimal("190")
    assert variants["SKU-BEIGE"].stock_balance == Decimal("12")
    assert variants["SKU-BEIGE"].sale_quantity == Decimal("11")
    assert variants["SKU-BEIGE"].rental_asset_count == 1
    assert variants["SKU-BEIGE"].sale_row_visible is True
    assert variants["SKU-BEIGE"].rental_row_visible is True
    assert variants["SKU-BEIGE"].aqsi_status is PublicationStatus.PUBLISHED
    assert variants["SKU-BEIGE"].aqsi_is_current is False
    assert variants["SKU-GRAY"].current_retail_price is None
    assert variants["SKU-GRAY"].stock_balance == Decimal("0")
    assert variants["SKU-GRAY"].sale_row_visible is True
    assert variants["SKU-GRAY"].rental_row_visible is False
    assert variants["SKU-GRAY"].aqsi_status is None
    assert variants["SKU-BLACK"].current_retail_price == Decimal("420")
    assert variants["SKU-BLACK"].stock_balance == Decimal("-2")
    assert variants["SKU-BLACK"].sale_quantity == Decimal("-2")
    assert variants["SKU-BLACK"].sale_row_visible is True
    assert variants["SKU-BLACK"].rental_row_visible is False
    assert variants["SKU-BLACK"].aqsi_status is PublicationStatus.FAILED
    assert "SKU-ARCHIVED" not in variants


def test_product_card_query_count_does_not_grow_with_products_or_assets(
    session: Session,
    catalog_data: dict[str, object],
) -> None:
    engine = session.get_bind()
    statements: list[str] = []

    def record_query(*args: object) -> None:
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record_query)
    try:
        RentalOperationsReadService(session).list_products(mode=CatalogMode.ALL)
        baseline = len(statements)

        category_id = catalog_data["sale_product"].category_id
        product = CatalogProduct(title="Много вариантов", slug="many", category_id=category_id)
        session.add(product)
        session.flush()
        variants = [
            CatalogVariant(
                product_id=product.id,
                title=f"Вариант {index}",
                sku=f"SKU-MANY-{index}",
                barcode=f"46000000001{index:02d}",
            )
            for index in range(8)
        ]
        session.add_all(variants)
        session.flush()
        session.add_all(
            [
                RentalAssetRecord(
                    asset_number=f"RENT-MANY-{index:03d}",
                    variant_id=variant.id,
                    purpose=AssetPurpose.RENTAL,
                    condition=AssetCondition.NEW,
                    availability=RentalAvailability.AVAILABLE,
                )
                for index, variant in enumerate(variants)
            ]
        )
        session.commit()
        statements.clear()

        RentalOperationsReadService(session).list_products(mode=CatalogMode.ALL)

        assert len(statements) == baseline
    finally:
        event.remove(engine, "before_cursor_execute", record_query)

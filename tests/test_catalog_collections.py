from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.catalog.collection_enums import (
    CollectionRuleField,
    CollectionRuleOperator,
    CollectionType,
)
from core.catalog.collection_models import (
    CatalogCollection,
    CollectionMembership,
    CollectionRule,
)
from core.catalog.collection_schemas import (
    CollectionCreate,
    CollectionRuleWrite,
    CollectionUpdate,
)
from core.catalog.collection_service import (
    CollectionMembershipError,
    CollectionNotFoundError,
    CollectionRuleValidationError,
    CollectionService,
    CollectionTypeChangeError,
)
from core.catalog.models import (
    CatalogProduct,
    CatalogVariant,
    CatalogVariantBarcode,
    Category,
)
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.main import create_app
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the complete persistence slice used by live Collection queries."""
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
            StockMovement.__table__,
            CatalogCollection.__table__,
            CollectionMembership.__table__,
            CollectionRule.__table__,
        ],
    )
    with sessionmaker(bind=engine, autoflush=False)() as value:
        yield value


@pytest.fixture
def admin(session: Session) -> User:
    """Create the authenticated operator used by Collection route tests."""
    user = User(
        email="collections@example.test",
        full_name="Collections Operator",
        password_hash="unused",
        is_admin=True,
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def client(session: Session, admin: User) -> Generator[TestClient]:
    """Expose Collection endpoints with request authentication overridden."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def catalog(session: Session) -> dict[str, object]:
    """Create Variants whose category, price, stock, and activity differ."""
    toys = Category(title="Игрушки", slug="toys")
    decor = Category(title="Декор", slug="decor")
    session.add_all([toys, decor])
    session.flush()
    bear = CatalogProduct(title="Мишка", slug="bear", category_id=toys.id)
    vase = CatalogProduct(title="Ваза", slug="vase", category_id=decor.id)
    session.add_all([bear, vase])
    session.flush()
    small = CatalogVariant(
        product_id=bear.id,
        title="Маленький",
        sku="SKU-BEAR-S",
        barcode="2000000000001",
    )
    large = CatalogVariant(
        product_id=bear.id,
        title="Большой",
        sku="SKU-BEAR-L",
        barcode="2000000000002",
    )
    inactive = CatalogVariant(
        product_id=vase.id,
        title="Старая",
        sku="SKU-VASE-OLD",
        barcode="2000000000003",
        is_active=False,
    )
    session.add_all([small, large, inactive])
    session.flush()
    past = datetime.now(UTC) - timedelta(days=1)
    session.add_all(
        [
            Price(
                variant_id=small.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("500.00"),
                effective_from=past,
            ),
            Price(
                variant_id=large.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("1200.00"),
                effective_from=past,
            ),
            Price(
                variant_id=inactive.id,
                price_type=PriceType.RETAIL,
                amount=Decimal("300.00"),
                effective_from=past,
            ),
            StockMovement(
                variant_id=small.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("5"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=large.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("1"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
            StockMovement(
                variant_id=inactive.id,
                movement_type=MovementType.RECEIPT,
                quantity_delta=Decimal("8"),
                source_type=SourceType.RECEIPT,
                source_id=generate_uuid_v7(),
            ),
        ]
    )
    session.commit()
    return {
        "toys": toys,
        "decor": decor,
        "small": small,
        "large": large,
        "inactive": inactive,
    }


def _rule(
    field: CollectionRuleField,
    operator: CollectionRuleOperator,
    value: str | Decimal | int | bool,
) -> CollectionRuleWrite:
    return CollectionRuleWrite(field=field, operator=operator, value=value)


def _create_static(service: CollectionService, slug: str = "favorites") -> CatalogCollection:
    created = service.create_collection(
        CollectionCreate(name="Избранное", slug=slug, type=CollectionType.STATIC)
    )
    collection = service._session.get(CatalogCollection, created.id)  # noqa: SLF001
    assert collection is not None
    return collection


def test_static_collection_create_update_disable_and_default_visibility(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    created = service.create_collection(
        CollectionCreate(
            name="Витрина",
            slug="showcase",
            type=CollectionType.STATIC,
            description="Ручной выбор",
            sort_order=5,
        )
    )
    session.commit()

    assert created.variant_count == 0
    assert [item.slug for item in service.list_collections()] == ["showcase"]

    updated = service.update_collection(
        created.id,
        CollectionUpdate(name="Главная витрина", is_active=False),
    )
    session.commit()

    assert updated.name == "Главная витрина"
    assert service.list_collections() == []
    assert [item.slug for item in service.list_collections(include_inactive=True)] == [
        "showcase"
    ]


def test_collection_slug_is_generated_from_name_and_collision_is_deterministic(
    session: Session,
) -> None:
    service = CollectionService(session)

    first = service.create_collection(
        CollectionCreate(name="Всё за 50", type=CollectionType.STATIC)
    )
    second = service.create_collection(
        CollectionCreate(name="Всё за 50", type=CollectionType.STATIC)
    )
    session.commit()

    assert first.slug == "vse-za-50"
    assert second.slug == "vse-za-50-2"


def test_static_membership_is_unique_and_supports_multiple_variants(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    collection = _create_static(service)
    small = catalog["small"]
    large = catalog["large"]
    service.add_members(collection.id, [small.id, large.id])
    session.commit()

    detail = service.get_collection(collection.id)
    assert detail.variant_count == 2
    assert [item.id for item in service.get_collection_variants(collection.id)] == [
        small.id,
        large.id,
    ]

    second = _create_static(service, "second-static")
    service.add_members(second.id, [small.id])
    session.commit()
    memberships = session.scalars(
        select(CollectionMembership).where(CollectionMembership.variant_id == small.id)
    ).all()
    assert {membership.collection_id for membership in memberships} == {
        collection.id,
        second.id,
    }
    assert [item.id for item in service.get_collection_variants(second.id)] == [small.id]

    with pytest.raises(CollectionMembershipError, match="already a member"):
        service.add_members(collection.id, [small.id])

    service.remove_member(collection.id, small.id)
    session.commit()
    assert service.get_collection(collection.id).variant_count == 1
    assert session.scalar(
        select(CollectionMembership).where(
            CollectionMembership.collection_id == collection.id,
            CollectionMembership.variant_id == small.id,
        )
    ) is None
    assert session.get(CatalogVariant, small.id) is not None


def test_database_rejects_duplicate_static_membership(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    collection = _create_static(service, "unique-members")
    variant = catalog["small"]
    session.add_all(
        [
            CollectionMembership(collection_id=collection.id, variant_id=variant.id),
            CollectionMembership(collection_id=collection.id, variant_id=variant.id),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_dynamic_price_rule_updates_live_without_membership_rows(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    created = service.create_collection(
        CollectionCreate(
            name="До 600 ₽",
            slug="under-600",
            type=CollectionType.DYNAMIC,
            rules=[
                _rule(
                    CollectionRuleField.PRICE,
                    CollectionRuleOperator.LTE,
                    Decimal("600"),
                )
            ],
        )
    )
    session.commit()

    assert [item.id for item in service.get_collection_variants(created.id)] == [
        catalog["inactive"].id,
        catalog["small"].id,
    ]
    assert session.scalars(select(CollectionMembership)).all() == []

    session.add(
        Price(
            variant_id=catalog["large"].id,
            price_type=PriceType.RETAIL,
            amount=Decimal("550"),
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()

    assert {item.id for item in service.get_collection_variants(created.id)} == {
        catalog["small"].id,
        catalog["large"].id,
        catalog["inactive"].id,
    }

    session.add(
        Price(
            variant_id=catalog["large"].id,
            price_type=PriceType.RETAIL,
            amount=Decimal("1300"),
            effective_from=datetime.now(UTC),
        )
    )
    session.commit()
    assert {item.id for item in service.get_collection_variants(created.id)} == {
        catalog["small"].id,
        catalog["inactive"].id,
    }


def test_dynamic_collection_result_uses_product_variant_order(
    session: Session,
    catalog: dict[str, object],
) -> None:
    """Computed Collection results use Product order rather than membership order."""
    small = catalog["small"]
    large = catalog["large"]
    small.sort_order = 20
    large.sort_order = 10
    service = CollectionService(session)
    collection = service.create_collection(
        CollectionCreate(
            name="Активные игрушки",
            type=CollectionType.DYNAMIC,
            rules=[
                _rule(
                    CollectionRuleField.IS_ACTIVE,
                    CollectionRuleOperator.EQ,
                    True,
                )
            ],
        )
    )
    session.commit()

    assert [item.id for item in service.get_collection_variants(collection.id)] == [
        large.id,
        small.id,
    ]


def test_dynamic_rules_use_and_with_category_stock_and_activity(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    created = service.create_collection(
        CollectionCreate(
            name="Игрушки в наличии",
            slug="toys-in-stock",
            type=CollectionType.DYNAMIC,
            rules=[
                _rule(
                    CollectionRuleField.CATEGORY,
                    CollectionRuleOperator.EQ,
                    str(catalog["toys"].id),
                ),
                _rule(CollectionRuleField.STOCK, CollectionRuleOperator.GTE, 2),
                _rule(CollectionRuleField.IS_ACTIVE, CollectionRuleOperator.EQ, True),
            ],
        )
    )
    session.commit()

    matches = service.get_collection_variants(created.id)

    assert [item.id for item in matches] == [catalog["small"].id]
    assert service.get_collection(created.id).variant_count == 1


def test_category_rule_expands_product_taxonomy_to_all_its_variants(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    created = service.create_collection(
        CollectionCreate(
            name="Все игрушки",
            slug="all-toys",
            type=CollectionType.DYNAMIC,
            rules=[
                _rule(
                    CollectionRuleField.CATEGORY,
                    CollectionRuleOperator.EQ,
                    str(catalog["toys"].id),
                )
            ],
        )
    )
    session.commit()

    assert {item.id for item in service.get_collection_variants(created.id)} == {
        catalog["small"].id,
        catalog["large"].id,
    }


def test_static_and_dynamic_membership_coexist_on_one_variant(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    static = _create_static(service, "manual-pick")
    service.add_members(static.id, [catalog["small"].id])
    dynamic = service.create_collection(
        CollectionCreate(
            name="Есть остаток",
            slug="has-stock",
            type=CollectionType.DYNAMIC,
            rules=[_rule(CollectionRuleField.STOCK, CollectionRuleOperator.GT, 0)],
        )
    )
    session.commit()

    variant_state = service.get_variant_collections(catalog["small"].id)

    assert variant_state.static_collections[0].is_member is True
    assert [item.id for item in variant_state.dynamic_collections] == [dynamic.id]


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (
            _rule(CollectionRuleField.CATEGORY, CollectionRuleOperator.GT, "bad"),
            "supports EQ only",
        ),
        (
            _rule(CollectionRuleField.IS_ACTIVE, CollectionRuleOperator.LT, True),
            "supports EQ only",
        ),
        (
            _rule(CollectionRuleField.STOCK, CollectionRuleOperator.EQ, "1.5"),
            "requires an integer",
        ),
        (
            _rule(CollectionRuleField.PRICE, CollectionRuleOperator.EQ, "-1"),
            "cannot be negative",
        ),
    ],
)
def test_invalid_rules_are_rejected(
    session: Session,
    catalog: dict[str, object],
    rule: CollectionRuleWrite,
    message: str,
) -> None:
    service = CollectionService(session)

    with pytest.raises(CollectionRuleValidationError, match=message):
        service.create_collection(
            CollectionCreate(
                name="Некорректная",
                slug=f"invalid-{rule.field.value.replace('_', '-')}-{rule.operator.value}",
                type=CollectionType.DYNAMIC,
                rules=[rule],
            )
        )


def test_empty_dynamic_rules_and_collection_type_changes_are_blocked(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)

    with pytest.raises(CollectionRuleValidationError, match="requires at least one"):
        service.create_collection(
            CollectionCreate(name="Пустая", slug="empty", type=CollectionType.DYNAMIC)
        )

    static = _create_static(service, "fixed-type")
    with pytest.raises(CollectionTypeChangeError):
        service.update_collection(
            static.id,
            CollectionUpdate(type=CollectionType.DYNAMIC),
        )


def test_inactive_collection_is_hidden_from_variant_card_matches(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    created = service.create_collection(
        CollectionCreate(
            name="Скрытая",
            slug="hidden",
            type=CollectionType.DYNAMIC,
            is_active=False,
            rules=[_rule(CollectionRuleField.STOCK, CollectionRuleOperator.GT, 0)],
        )
    )
    session.commit()

    assert service.list_collections() == []
    assert service.list_collections(include_inactive=True)[0].id == created.id
    assert service.get_variant_collections(catalog["small"].id).dynamic_collections == []


def test_collection_routes_create_preview_and_manage_static_members(
    client: TestClient,
    session: Session,
    catalog: dict[str, object],
) -> None:
    dynamic = client.post(
        "/api/catalog/collections",
        json={
            "name": "Недорогие игрушки",
            "slug": "affordable-toys",
            "type": "dynamic",
            "rules": [
                {"field": "category", "operator": "eq", "value": str(catalog["toys"].id)},
                {"field": "price", "operator": "lte", "value": "600.00"},
            ],
        },
    )
    preview = client.post(
        "/api/catalog/collections/preview",
        json={"rules": [{"field": "stock", "operator": "gte", "value": 5}]},
    )
    static = client.post(
        "/api/catalog/collections",
        json={"name": "Ручной выбор", "type": "static"},
    )
    added = client.post(
        f"/api/catalog/collections/{static.json()['id']}/memberships",
        json={"variant_ids": [str(catalog["small"].id)]},
    )
    members = client.get(
        f"/api/catalog/collections/{static.json()['id']}/variants"
    )

    assert dynamic.status_code == 201
    assert dynamic.json()["variant_count"] == 1
    assert preview.status_code == 200
    assert preview.json()["count"] == 2
    assert added.status_code == 200
    assert added.json()["variant_count"] == 1
    assert [item["id"] for item in members.json()] == [str(catalog["small"].id)]
    deleted = client.delete(f"/api/catalog/collections/{static.json()['id']}")
    assert deleted.status_code == 204
    assert session.get(CatalogVariant, catalog["small"].id) is not None


def test_delete_collection_removes_owned_rows_but_not_catalog_entities(
    session: Session,
    catalog: dict[str, object],
) -> None:
    service = CollectionService(session)
    static = service.create_collection(
        CollectionCreate(name="На кассе", type=CollectionType.STATIC)
    )
    service.add_members(static.id, [catalog["small"].id])
    dynamic = service.create_collection(
        CollectionCreate(
            name="В наличии",
            type=CollectionType.DYNAMIC,
            rules=[_rule(CollectionRuleField.STOCK, CollectionRuleOperator.GT, 0)],
        )
    )
    session.commit()

    service.delete_collection(static.id)
    service.delete_collection(dynamic.id)
    session.commit()

    assert session.get(CatalogCollection, static.id) is None
    assert session.get(CatalogCollection, dynamic.id) is None
    assert session.scalars(select(CollectionMembership)).all() == []
    assert session.scalars(select(CollectionRule)).all() == []
    assert session.get(CatalogVariant, catalog["small"].id) is not None
    assert session.get(CatalogProduct, catalog["small"].product_id) is not None
    with pytest.raises(CollectionNotFoundError):
        service.get_collection(static.id)

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Select, and_, delete, func, not_, or_, select
from sqlalchemy.orm import Session

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
from core.catalog.models import CatalogProduct, CatalogVariant
from core.catalog.ordering import variant_ordering_expressions
from core.inventory.models import StockMovement
from core.pricing.enums import PriceType
from core.pricing.models import Price
from core.shared.db import UUIDv7

type VariantProjectionRow = tuple[
    CatalogVariant,
    CatalogProduct,
    Decimal | None,
    Decimal,
]


class CollectionRepository:
    """Persistence and live evaluation queries for Catalog Collections."""

    def __init__(self, session: Session) -> None:
        """Bind Collection persistence to one request transaction."""
        self._session = session

    def add(self, collection: CatalogCollection) -> CatalogCollection:
        """Stage one new Collection."""
        self._session.add(collection)
        return collection

    def delete(self, collection: CatalogCollection) -> None:
        """Delete one Collection and only its owned rules and memberships."""
        self._session.delete(collection)

    def get(self, collection_id: UUIDv7, *, for_update: bool = False) -> CatalogCollection | None:
        """Return one non-deleted Collection, optionally locked for mutation."""
        statement = select(CatalogCollection).where(
            CatalogCollection.id == collection_id,
            CatalogCollection.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def get_by_slug(self, slug: str) -> CatalogCollection | None:
        """Return the Collection owning a globally stable slug."""
        return self._session.scalar(
            select(CatalogCollection).where(CatalogCollection.slug == slug)
        )

    def list(self, *, include_inactive: bool) -> Sequence[CatalogCollection]:
        """Return active Collections by default and all rows for management."""
        statement = select(CatalogCollection).where(CatalogCollection.deleted_at.is_(None))
        if not include_inactive:
            statement = statement.where(CatalogCollection.is_active.is_(True))
        return self._session.scalars(
            statement.order_by(
                CatalogCollection.sort_order,
                CatalogCollection.name,
                CatalogCollection.id,
            )
        ).all()

    def list_active_static(self) -> Sequence[CatalogCollection]:
        """Return STATIC Collections editable from a Variant card."""
        return self._session.scalars(
            select(CatalogCollection)
            .where(
                CatalogCollection.deleted_at.is_(None),
                CatalogCollection.is_active.is_(True),
                CatalogCollection.type == CollectionType.STATIC,
            )
            .order_by(CatalogCollection.sort_order, CatalogCollection.name)
        ).all()

    def add_rule(self, rule: CollectionRule) -> CollectionRule:
        """Stage one validated typed rule."""
        self._session.add(rule)
        return rule

    def replace_rules(
        self,
        collection_id: UUIDv7,
        rules: Sequence[CollectionRule],
    ) -> None:
        """Replace one DYNAMIC Collection's complete AND-rule set."""
        self._session.execute(
            delete(CollectionRule).where(CollectionRule.collection_id == collection_id)
        )
        self._session.add_all(rules)

    def add_membership(self, membership: CollectionMembership) -> CollectionMembership:
        """Stage one explicit STATIC membership."""
        self._session.add(membership)
        return membership

    def get_membership(
        self,
        collection_id: UUIDv7,
        variant_id: UUIDv7,
    ) -> CollectionMembership | None:
        """Return one exact STATIC membership when present."""
        return self._session.scalar(
            select(CollectionMembership).where(
                CollectionMembership.collection_id == collection_id,
                CollectionMembership.variant_id == variant_id,
            )
        )

    def list_variant_memberships(self, variant_id: UUIDv7) -> Sequence[CollectionMembership]:
        """Return all explicit Collection links for one Variant."""
        return self._session.scalars(
            select(CollectionMembership).where(CollectionMembership.variant_id == variant_id)
        ).all()

    def remove_membership(self, membership: CollectionMembership) -> None:
        """Remove only the association, never its Product or Variant."""
        self._session.delete(membership)

    def count_static_members(self, collection_id: UUIDv7) -> int:
        """Count persisted explicit memberships."""
        return int(
            self._session.scalar(
                select(func.count(CollectionMembership.id)).where(
                    CollectionMembership.collection_id == collection_id
                )
            )
            or 0
        )

    def count_dynamic_members(self, rules: Sequence[CollectionRule]) -> int:
        """Count current Variants matching all typed live-state rules."""
        statement = select(func.count(CatalogVariant.id)).join(
            CatalogProduct,
            CatalogProduct.id == CatalogVariant.product_id,
        )
        statement = self._apply_live_scope(statement)
        statement = statement.where(*self._rule_conditions(rules))
        return int(self._session.scalar(statement) or 0)

    def dynamic_contains_variant(
        self,
        variant_id: UUIDv7,
        rules: Sequence[CollectionRule],
    ) -> bool:
        """Check one Variant against live rules without loading the whole Collection."""
        statement = select(CatalogVariant.id).join(
            CatalogProduct,
            CatalogProduct.id == CatalogVariant.product_id,
        )
        statement = self._apply_live_scope(statement).where(
            CatalogVariant.id == variant_id,
            *self._rule_conditions(rules),
        )
        return self._session.scalar(statement.limit(1)) is not None

    def list_static_variants(
        self,
        collection_id: UUIDv7,
        *,
        limit: int | None = None,
    ) -> Sequence[VariantProjectionRow]:
        """Return explicit STATIC members with live price and stock projections."""
        statement = (
            self._variant_projection_statement()
            .join(
                CollectionMembership,
                CollectionMembership.variant_id == CatalogVariant.id,
            )
            .where(CollectionMembership.collection_id == collection_id)
            .order_by(
                CollectionMembership.sort_order,
                CollectionMembership.created_at,
                CatalogVariant.id,
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self._session.execute(statement).all()

    def list_dynamic_variants(
        self,
        rules: Sequence[CollectionRule],
        *,
        limit: int | None = None,
    ) -> Sequence[VariantProjectionRow]:
        """Evaluate DYNAMIC membership directly from current domain facts."""
        statement = self._apply_live_scope(self._variant_projection_statement())
        statement = statement.where(*self._rule_conditions(rules)).order_by(
            CatalogProduct.title,
            *variant_ordering_expressions(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        return self._session.execute(statement).all()

    def search_variants(self, query: str, *, limit: int) -> Sequence[VariantProjectionRow]:
        """Find operational Variants for the STATIC add-members flow."""
        terms = query.strip().split()
        statement = self._apply_live_scope(self._variant_projection_statement()).where(
            CatalogVariant.is_active.is_(True),
            CatalogProduct.is_active.is_(True),
            and_(
                *(
                    or_(
                        CatalogProduct.title.ilike(f"%{term}%"),
                        CatalogVariant.title.ilike(f"%{term}%"),
                        CatalogVariant.sku.ilike(f"%{term}%"),
                        CatalogVariant.barcode.ilike(f"%{term}%"),
                    )
                    for term in terms
                )
            ),
        )
        return self._session.execute(
            statement.order_by(
                CatalogProduct.title,
                *variant_ordering_expressions(),
            ).limit(limit)
        ).all()

    def variant_projection(self, variant_id: UUIDv7) -> VariantProjectionRow | None:
        """Return one existing Variant with the same live facts used by evaluation."""
        return self._session.execute(
            self._variant_projection_statement().where(CatalogVariant.id == variant_id)
        ).one_or_none()

    @staticmethod
    def _apply_live_scope(statement: Select[tuple[object, ...]]) -> Select[tuple[object, ...]]:
        """Exclude archived Product/Variant rows while keeping is_active rule-addressable."""
        return statement.where(
            CatalogVariant.deleted_at.is_(None),
            CatalogProduct.deleted_at.is_(None),
        )

    @staticmethod
    def _current_price_expression() -> object:
        """Return the current RETAIL Price as a correlated Decimal scalar."""
        return (
            select(Price.amount)
            .where(
                Price.variant_id == CatalogVariant.id,
                Price.price_type == PriceType.RETAIL,
                Price.effective_from <= datetime.now(UTC),
            )
            .order_by(Price.effective_from.desc(), Price.created_at.desc(), Price.id.desc())
            .limit(1)
            .correlate(CatalogVariant)
            .scalar_subquery()
        )

    @staticmethod
    def _stock_expression() -> object:
        """Return authoritative ledger balance as a correlated Decimal scalar."""
        return (
            select(func.coalesce(func.sum(StockMovement.quantity_delta), Decimal("0")))
            .where(StockMovement.variant_id == CatalogVariant.id)
            .correlate(CatalogVariant)
            .scalar_subquery()
        )

    def _variant_projection_statement(self) -> Select[tuple[object, ...]]:
        """Build the shared concrete Variant projection without hiding archived rows."""
        return select(
            CatalogVariant,
            CatalogProduct,
            self._current_price_expression().label("current_retail_price"),
            self._stock_expression().label("stock"),
        ).join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)

    def _rule_conditions(self, rules: Sequence[CollectionRule]) -> list[object]:
        """Compile validated typed rules to SQLAlchemy comparisons combined by AND."""
        conditions: list[object] = []
        price = self._current_price_expression()
        stock = self._stock_expression()
        for rule in rules:
            if rule.field is CollectionRuleField.PRICE:
                conditions.append(self._compare(price, rule.operator, rule.price_value))
            elif rule.field is CollectionRuleField.STOCK:
                conditions.append(self._compare(stock, rule.operator, rule.stock_value))
            elif rule.field is CollectionRuleField.IS_ACTIVE:
                operational = and_(
                    CatalogVariant.is_active.is_(True),
                    CatalogProduct.is_active.is_(True),
                )
                conditions.append(operational if rule.boolean_value else not_(operational))
            else:
                conditions.append(CatalogProduct.category_id == rule.category_id)
        return conditions

    @staticmethod
    def _compare(expression: object, operator: CollectionRuleOperator, value: object) -> object:
        """Apply one whitelisted comparison without accepting arbitrary SQL."""
        if operator is CollectionRuleOperator.EQ:
            return expression == value
        if operator is CollectionRuleOperator.LT:
            return expression < value
        if operator is CollectionRuleOperator.LTE:
            return expression <= value
        if operator is CollectionRuleOperator.GT:
            return expression > value
        return expression >= value

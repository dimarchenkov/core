from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

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
from core.catalog.collection_repository import CollectionRepository, VariantProjectionRow
from core.catalog.collection_schemas import (
    CollectionCreate,
    CollectionDetailRead,
    CollectionPreviewRead,
    CollectionRuleRead,
    CollectionRuleWrite,
    CollectionSummaryRead,
    CollectionUpdate,
    CollectionVariantRead,
    VariantCollectionOptionRead,
    VariantCollectionsRead,
)
from core.catalog.repository import CatalogVariantRepository, CategoryRepository
from core.shared.db import UUIDv7
from core.shared.money import quantize_money


class CollectionNotFoundError(Exception):
    """Raised when a Collection does not exist in the active database graph."""


class CollectionSlugConflictError(Exception):
    """Raised when a stable Collection slug already belongs to another row."""


class CollectionTypeChangeError(Exception):
    """Raised when an immutable STATIC/DYNAMIC strategy change is requested."""


class CollectionRuleValidationError(Exception):
    """Raised when a DYNAMIC rule has an invalid operator or typed value."""


class CollectionMembershipError(Exception):
    """Raised for invalid, duplicate, or wrong-type STATIC membership commands."""


class CollectionVariantNotFoundError(Exception):
    """Raised when a membership target Variant does not exist."""


class CollectionService:
    """Application workflows for STATIC and live-evaluated DYNAMIC Collections."""

    def __init__(self, session: Session) -> None:
        """Bind Collection workflows to a route-owned transaction."""
        self._session = session
        self._repository = CollectionRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._categories = CategoryRepository(session)

    def create_collection(
        self,
        data: CollectionCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionDetailRead:
        """Create one Collection and validate its complete initial rule strategy."""
        slug = data.slug or self._unique_slug(data.name)
        self._ensure_slug_available(slug)
        if data.type is CollectionType.STATIC and data.rules:
            raise CollectionRuleValidationError("STATIC collection cannot contain rules.")
        if data.type is CollectionType.DYNAMIC and not data.rules:
            raise CollectionRuleValidationError("DYNAMIC collection requires at least one rule.")
        collection = CatalogCollection(
            name=data.name,
            slug=slug,
            type=data.type,
            is_active=data.is_active,
            description=data.description,
            sort_order=data.sort_order,
            created_by_id=actor_id,
        )
        self._repository.add(collection)
        self._session.flush()
        for rule_data in data.rules:
            self._repository.add_rule(
                self._build_rule(collection.id, rule_data, actor_id=actor_id)
            )
        self._session.flush()
        return self.get_collection(collection.id)

    def delete_collection(self, collection_id: UUIDv7) -> None:
        """Delete one isolated Collection without modifying Catalog entities."""
        collection = self._require_collection(collection_id, for_update=True)
        self._repository.delete(collection)
        self._session.flush()

    def list_collections(self, *, include_inactive: bool = False) -> list[CollectionSummaryRead]:
        """List Collections with exact current STATIC or DYNAMIC Variant counts."""
        return [
            self._summary(collection)
            for collection in self._repository.list(include_inactive=include_inactive)
        ]

    def get_collection(self, collection_id: UUIDv7) -> CollectionDetailRead:
        """Return one Collection with its current count and typed rules."""
        collection = self._require_collection(collection_id)
        return CollectionDetailRead(
            **self._summary(collection).model_dump(),
            rules=[self._rule_read(rule) for rule in collection.rules],
        )

    def update_collection(
        self,
        collection_id: UUIDv7,
        data: CollectionUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionDetailRead:
        """Update Collection metadata while keeping its membership strategy stable."""
        collection = self._require_collection(collection_id, for_update=True)
        changes = data.model_dump(exclude_unset=True)
        if data.type is not None and data.type is not collection.type:
            raise CollectionTypeChangeError
        if data.slug is not None and data.slug != collection.slug:
            self._ensure_slug_available(data.slug, current_id=collection.id)
        for field in ("name", "slug", "is_active", "description", "sort_order"):
            if field in changes:
                setattr(collection, field, changes[field])
        if actor_id is not None:
            collection.updated_by_id = actor_id
        self._session.flush()
        return self.get_collection(collection.id)

    def replace_rules(
        self,
        collection_id: UUIDv7,
        rules: Sequence[CollectionRuleWrite],
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionDetailRead:
        """Atomically replace all AND-conditions for one DYNAMIC Collection."""
        collection = self._require_collection(collection_id, for_update=True)
        if collection.type is not CollectionType.DYNAMIC:
            raise CollectionRuleValidationError("Only DYNAMIC collections have rules.")
        if not rules:
            raise CollectionRuleValidationError("DYNAMIC collection requires at least one rule.")
        built = [self._build_rule(collection.id, rule, actor_id=actor_id) for rule in rules]
        self._repository.replace_rules(collection.id, built)
        self._touch_collection(collection, actor_id)
        self._session.flush()
        self._session.expire(collection, ["rules"])
        return self.get_collection(collection.id)

    def add_members(
        self,
        collection_id: UUIDv7,
        variant_ids: Sequence[UUIDv7],
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionDetailRead:
        """Add selected concrete Variants to one STATIC Collection."""
        collection = self._require_static_collection(collection_id, for_update=True)
        if len(set(variant_ids)) != len(variant_ids):
            raise CollectionMembershipError("Duplicate Variant in request.")
        start_position = self._repository.count_static_members(collection.id)
        for position, variant_id in enumerate(variant_ids):
            if self._variants.get(variant_id) is None:
                raise CollectionVariantNotFoundError
            if self._repository.get_membership(collection.id, variant_id) is not None:
                raise CollectionMembershipError("Variant is already a member.")
            self._repository.add_membership(
                CollectionMembership(
                    collection_id=collection.id,
                    variant_id=variant_id,
                    sort_order=start_position + position,
                    created_by_id=actor_id,
                )
            )
        self._touch_collection(collection, actor_id)
        self._session.flush()
        return self.get_collection(collection.id)

    def remove_member(
        self,
        collection_id: UUIDv7,
        variant_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionDetailRead:
        """Remove only a STATIC association without touching Catalog entities."""
        collection = self._require_static_collection(collection_id, for_update=True)
        membership = self._repository.get_membership(collection.id, variant_id)
        if membership is None:
            raise CollectionMembershipError("Variant is not a member.")
        self._repository.remove_membership(membership)
        self._touch_collection(collection, actor_id)
        self._session.flush()
        return self.get_collection(collection.id)

    def get_collection_variants(
        self,
        collection_id: UUIDv7,
        *,
        limit: int | None = None,
    ) -> list[CollectionVariantRead]:
        """Expose one stable query boundary for future storefront and publishing clients."""
        collection = self._require_collection(collection_id)
        rows = (
            self._repository.list_static_variants(collection.id, limit=limit)
            if collection.type is CollectionType.STATIC
            else self._repository.list_dynamic_variants(collection.rules, limit=limit)
        )
        return [self._variant_read(row) for row in rows]

    def get_active_collection_variant_ids(self, collection_id: UUIDv7) -> set[UUIDv7]:
        """Resolve one active Collection for the normal Catalog filter."""
        collection = self._require_collection(collection_id)
        if not collection.is_active:
            raise CollectionNotFoundError
        rows = (
            self._repository.list_static_variants(collection.id)
            if collection.type is CollectionType.STATIC
            else self._repository.list_dynamic_variants(collection.rules)
        )
        return {variant.id for variant, _product, _price, _stock in rows}

    def preview_rules(
        self,
        rules: Sequence[CollectionRuleWrite],
        *,
        limit: int = 8,
    ) -> CollectionPreviewRead:
        """Validate unsaved rules and return an exact count with bounded sample rows."""
        if not rules:
            raise CollectionRuleValidationError("Preview requires at least one rule.")
        temporary_id = UUID(int=0)
        built = [self._build_rule(temporary_id, rule) for rule in rules]
        count = self._repository.count_dynamic_members(built)
        items = [
            self._variant_read(row)
            for row in self._repository.list_dynamic_variants(built, limit=limit)
        ]
        return CollectionPreviewRead(count=count, items=items)

    def search_variants(self, query: str, *, limit: int = 12) -> list[CollectionVariantRead]:
        """Return bounded Variant search results for explicit STATIC membership."""
        if not query.strip():
            return []
        return [
            self._variant_read(row)
            for row in self._repository.search_variants(query, limit=limit)
        ]

    def get_variant_collections(self, variant_id: UUIDv7) -> VariantCollectionsRead:
        """Return editable STATIC options and computed active DYNAMIC matches."""
        if self._variants.get(variant_id) is None:
            raise CollectionVariantNotFoundError
        memberships = {
            membership.collection_id
            for membership in self._repository.list_variant_memberships(variant_id)
        }
        static_options = [
            VariantCollectionOptionRead(
                id=collection.id,
                name=collection.name,
                is_member=collection.id in memberships,
            )
            for collection in self._repository.list_active_static()
        ]
        dynamic = []
        for collection in self._repository.list(include_inactive=False):
            if collection.type is not CollectionType.DYNAMIC:
                continue
            if self._repository.dynamic_contains_variant(variant_id, collection.rules):
                dynamic.append(self._summary(collection))
        return VariantCollectionsRead(
            static_collections=static_options,
            dynamic_collections=dynamic,
        )

    def replace_variant_static_collections(
        self,
        variant_id: UUIDv7,
        collection_ids: Sequence[UUIDv7],
        *,
        actor_id: UUIDv7 | None = None,
    ) -> VariantCollectionsRead:
        """Synchronize memberships among active STATIC Collections from a Variant card."""
        if self._variants.get(variant_id) is None:
            raise CollectionVariantNotFoundError
        desired = set(collection_ids)
        active_static = {
            collection.id: collection
            for collection in self._repository.list_active_static()
        }
        if not desired.issubset(active_static):
            raise CollectionMembershipError("Only active STATIC collections are editable here.")
        memberships = {
            membership.collection_id: membership
            for membership in self._repository.list_variant_memberships(variant_id)
        }
        changed_collections: set[UUIDv7] = set()
        for collection_id in active_static:
            membership = memberships.get(collection_id)
            if collection_id in desired and membership is None:
                self._repository.add_membership(
                    CollectionMembership(
                        collection_id=collection_id,
                        variant_id=variant_id,
                        created_by_id=actor_id,
                    )
                )
                changed_collections.add(collection_id)
            elif collection_id not in desired and membership is not None:
                self._repository.remove_membership(membership)
                changed_collections.add(collection_id)
        for collection_id in changed_collections:
            self._touch_collection(active_static[collection_id], actor_id)
        self._session.flush()
        return self.get_variant_collections(variant_id)

    def _summary(self, collection: CatalogCollection) -> CollectionSummaryRead:
        """Build one API summary with a count derived by the Collection strategy."""
        count = (
            self._repository.count_static_members(collection.id)
            if collection.type is CollectionType.STATIC
            else self._repository.count_dynamic_members(collection.rules)
        )
        return CollectionSummaryRead(
            id=collection.id,
            name=collection.name,
            slug=collection.slug,
            type=collection.type,
            is_active=collection.is_active,
            description=collection.description,
            sort_order=collection.sort_order,
            variant_count=count,
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    @staticmethod
    def _rule_read(rule: CollectionRule) -> CollectionRuleRead:
        """Expose exactly the typed value owned by a rule field."""
        value: str | Decimal | int | bool | UUIDv7
        if rule.field is CollectionRuleField.PRICE:
            value = rule.price_value or Decimal("0")
        elif rule.field is CollectionRuleField.STOCK:
            value = rule.stock_value if rule.stock_value is not None else 0
        elif rule.field is CollectionRuleField.IS_ACTIVE:
            value = rule.boolean_value if rule.boolean_value is not None else False
        else:
            value = rule.category_id or UUID(int=0)
        return CollectionRuleRead(
            id=rule.id,
            field=rule.field,
            operator=rule.operator,
            value=value,
        )

    @staticmethod
    def _variant_read(row: VariantProjectionRow) -> CollectionVariantRead:
        """Convert one shared SQL projection to an API row."""
        variant, product, price, stock = row
        return CollectionVariantRead(
            id=variant.id,
            product_id=product.id,
            product_name=product.title,
            variant_name=variant.title,
            sku=variant.sku,
            current_retail_price=price,
            stock=stock,
            is_active=(
                variant.deleted_at is None
                and product.deleted_at is None
                and variant.is_active
                and product.is_active
            ),
        )

    def _build_rule(
        self,
        collection_id: UUIDv7,
        data: CollectionRuleWrite,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> CollectionRule:
        """Validate field/operator compatibility and persist one typed value column."""
        numeric_operators = {
            CollectionRuleOperator.EQ,
            CollectionRuleOperator.LT,
            CollectionRuleOperator.LTE,
            CollectionRuleOperator.GT,
            CollectionRuleOperator.GTE,
        }
        if data.field in {CollectionRuleField.CATEGORY, CollectionRuleField.IS_ACTIVE}:
            if data.operator is not CollectionRuleOperator.EQ:
                raise CollectionRuleValidationError(f"{data.field.value} supports EQ only.")
        elif data.operator not in numeric_operators:
            raise CollectionRuleValidationError("Unsupported numeric operator.")

        values: dict[str, Decimal | int | bool | UUIDv7 | None] = {
            "price_value": None,
            "stock_value": None,
            "boolean_value": None,
            "category_id": None,
        }
        if data.field is CollectionRuleField.PRICE:
            values["price_value"] = self._money_value(data.value)
        elif data.field is CollectionRuleField.STOCK:
            values["stock_value"] = self._integer_value(data.value)
        elif data.field is CollectionRuleField.IS_ACTIVE:
            values["boolean_value"] = self._boolean_value(data.value)
        else:
            category_id = self._category_value(data.value)
            category = self._categories.get(category_id)
            if category is None or not category.is_active:
                raise CollectionRuleValidationError(
                    "Category rule references an unavailable Category."
                )
            values["category_id"] = category_id

        rule = CollectionRule(
            collection_id=collection_id,
            field=data.field,
            operator=data.operator,
            created_by_id=actor_id,
            **values,
        )
        return rule

    @staticmethod
    def _money_value(value: str | Decimal | int | bool | UUIDv7) -> Decimal:
        """Parse a non-negative rule amount using Core Decimal money conventions."""
        if isinstance(value, bool):
            raise CollectionRuleValidationError("PRICE requires a money value.")
        try:
            amount = quantize_money(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CollectionRuleValidationError("PRICE requires a money value.") from exc
        if amount < 0:
            raise CollectionRuleValidationError("PRICE cannot be negative.")
        return amount

    @staticmethod
    def _integer_value(value: str | Decimal | int | bool | UUIDv7) -> int:
        """Parse STOCK as an exact integer rather than a free-form string."""
        if isinstance(value, bool) or isinstance(value, UUID):
            raise CollectionRuleValidationError("STOCK requires an integer.")
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise CollectionRuleValidationError("STOCK requires an integer.") from exc
        if decimal != decimal.to_integral_value():
            raise CollectionRuleValidationError("STOCK requires an integer.")
        return int(decimal)

    @staticmethod
    def _boolean_value(value: str | Decimal | int | bool | UUIDv7) -> bool:
        """Parse only explicit booleans for IS_ACTIVE."""
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
        raise CollectionRuleValidationError("IS_ACTIVE requires true or false.")

    @staticmethod
    def _category_value(value: str | Decimal | int | bool | UUIDv7) -> UUIDv7:
        """Parse CATEGORY as a real UUID reference."""
        if isinstance(value, UUID):
            return value
        if not isinstance(value, str):
            raise CollectionRuleValidationError("CATEGORY requires a Category id.")
        try:
            return UUID(value)
        except ValueError as exc:
            raise CollectionRuleValidationError("CATEGORY requires a Category id.") from exc

    def _require_collection(
        self,
        collection_id: UUIDv7,
        *,
        for_update: bool = False,
    ) -> CatalogCollection:
        """Return one Collection or raise a stable application error."""
        collection = self._repository.get(collection_id, for_update=for_update)
        if collection is None:
            raise CollectionNotFoundError
        return collection

    def _require_static_collection(
        self,
        collection_id: UUIDv7,
        *,
        for_update: bool = False,
    ) -> CatalogCollection:
        """Require an existing STATIC Collection for explicit membership commands."""
        collection = self._require_collection(collection_id, for_update=for_update)
        if collection.type is not CollectionType.STATIC:
            raise CollectionMembershipError("DYNAMIC membership is computed from rules.")
        return collection

    def _ensure_slug_available(
        self,
        slug: str,
        *,
        current_id: UUIDv7 | None = None,
    ) -> None:
        """Keep Collection slugs globally unique and stable."""
        owner = self._repository.get_by_slug(slug)
        if owner is not None and owner.id != current_id:
            raise CollectionSlugConflictError

    def _unique_slug(self, name: str) -> str:
        """Generate a readable stable slug and deterministically avoid collisions."""
        transliteration = str.maketrans(
            {
                "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
                "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
                "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
                "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
                "э": "e", "ю": "yu", "я": "ya",
            }
        )
        transliterated = name.casefold().translate(transliteration)
        base = re.sub(r"[^a-z0-9]+", "-", transliterated).strip("-") or "collection"
        base = base[:255].rstrip("-") or "collection"
        candidate = base
        suffix = 2
        while self._repository.get_by_slug(candidate) is not None:
            ending = f"-{suffix}"
            candidate = f"{base[: 255 - len(ending)].rstrip('-')}{ending}"
            suffix += 1
        return candidate

    @staticmethod
    def _touch_collection(
        collection: CatalogCollection,
        actor_id: UUIDv7 | None,
    ) -> None:
        """Reflect rule or membership changes in Collection management metadata."""
        collection.updated_at = datetime.now(UTC)
        if actor_id is not None:
            collection.updated_by_id = actor_id

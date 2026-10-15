from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.catalog.collection_enums import (
    CollectionRuleField,
    CollectionRuleOperator,
    CollectionType,
)
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type[StrEnum]) -> list[str]:
    """Persist stable lowercase enum values instead of Python member names."""
    return [member.value for member in enum_class]


class CatalogCollection(BaseModel):
    """Variant-level merchandising group independent from Product taxonomy."""

    __tablename__ = "catalog_collections"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    type: Mapped[CollectionType] = mapped_column(
        Enum(CollectionType, name="catalog_collection_type", values_callable=_enum_values),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    memberships: Mapped[list[CollectionMembership]] = relationship(
        "CollectionMembership",
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CollectionMembership.sort_order, CollectionMembership.created_at",
    )
    rules: Mapped[list[CollectionRule]] = relationship(
        "CollectionRule",
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CollectionRule.created_at, CollectionRule.id",
    )


class CollectionMembership(BaseModel):
    """Explicit membership of one concrete Variant in a STATIC Collection."""

    __tablename__ = "catalog_collection_memberships"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "variant_id",
            name="uq_collection_membership_collection_variant",
        ),
    )

    collection_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    collection: Mapped[CatalogCollection] = relationship(
        "CatalogCollection",
        back_populates="memberships",
    )


class CollectionRule(BaseModel):
    """One typed AND-condition belonging to a DYNAMIC Collection."""

    __tablename__ = "catalog_collection_rules"
    __table_args__ = (
        CheckConstraint(
            "(field = 'price' AND price_value IS NOT NULL AND stock_value IS NULL "
            "AND boolean_value IS NULL AND category_id IS NULL) OR "
            "(field = 'stock' AND price_value IS NULL AND stock_value IS NOT NULL "
            "AND boolean_value IS NULL AND category_id IS NULL) OR "
            "(field = 'is_active' AND price_value IS NULL AND stock_value IS NULL "
            "AND boolean_value IS NOT NULL AND category_id IS NULL) OR "
            "(field = 'category' AND price_value IS NULL AND stock_value IS NULL "
            "AND boolean_value IS NULL AND category_id IS NOT NULL)",
            name="ck_collection_rule_exactly_one_typed_value",
        ),
    )

    collection_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field: Mapped[CollectionRuleField] = mapped_column(
        Enum(
            CollectionRuleField,
            name="catalog_collection_rule_field",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    operator: Mapped[CollectionRuleOperator] = mapped_column(
        Enum(
            CollectionRuleOperator,
            name="catalog_collection_rule_operator",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    price_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    stock_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    category_id: Mapped[UUIDv7 | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    collection: Mapped[CatalogCollection] = relationship(
        "CatalogCollection",
        back_populates="rules",
    )

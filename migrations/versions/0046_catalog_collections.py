"""Add Variant-level STATIC and DYNAMIC Catalog Collections.

Revision ID: 0046_catalog_collections
Revises: 0045_aqsi_reconciliation
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0046_catalog_collections"
down_revision: str | None = "0045_aqsi_reconciliation"
branch_labels: str | None = None
depends_on: str | None = None

collection_type = sa.Enum("static", "dynamic", name="catalog_collection_type")
rule_field = sa.Enum(
    "price",
    "category",
    "stock",
    "is_active",
    name="catalog_collection_rule_field",
)
rule_operator = sa.Enum(
    "eq",
    "lt",
    "lte",
    "gt",
    "gte",
    name="catalog_collection_rule_operator",
)


def _audit_columns() -> list[sa.Column[object]]:
    """Return the shared entity columns used by Core's BaseModel."""
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
    ]


def _audit_constraints() -> list[sa.ForeignKeyConstraint]:
    """Return optional actor references used by every audited entity."""
    return [
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
    ]


def upgrade() -> None:
    """Create Collection metadata, explicit memberships, and typed AND-rules."""
    op.create_table(
        "catalog_collections",
        *_audit_columns(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("type", collection_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *_audit_constraints(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_catalog_collections_slug"),
    )
    op.create_index("ix_catalog_collections_slug", "catalog_collections", ["slug"])
    op.create_index("ix_catalog_collections_type", "catalog_collections", ["type"])
    op.create_index(
        "ix_catalog_collections_is_active",
        "catalog_collections",
        ["is_active"],
    )

    op.create_table(
        "catalog_collection_memberships",
        *_audit_columns(),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        *_audit_constraints(),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["catalog_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"],
            ["catalog_variants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collection_id",
            "variant_id",
            name="uq_collection_membership_collection_variant",
        ),
    )
    op.create_index(
        "ix_catalog_collection_memberships_collection_id",
        "catalog_collection_memberships",
        ["collection_id"],
    )
    op.create_index(
        "ix_catalog_collection_memberships_variant_id",
        "catalog_collection_memberships",
        ["variant_id"],
    )

    op.create_table(
        "catalog_collection_rules",
        *_audit_columns(),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("field", rule_field, nullable=False),
        sa.Column("operator", rule_operator, nullable=False),
        sa.Column("price_value", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stock_value", sa.Integer(), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        *_audit_constraints(),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["catalog_collections.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_collection_rules_collection_id",
        "catalog_collection_rules",
        ["collection_id"],
    )
    op.create_index(
        "ix_catalog_collection_rules_category_id",
        "catalog_collection_rules",
        ["category_id"],
    )


def downgrade() -> None:
    """Remove Catalog Collection persistence and its enum types."""
    op.drop_index(
        "ix_catalog_collection_rules_category_id",
        table_name="catalog_collection_rules",
    )
    op.drop_index(
        "ix_catalog_collection_rules_collection_id",
        table_name="catalog_collection_rules",
    )
    op.drop_table("catalog_collection_rules")
    op.drop_index(
        "ix_catalog_collection_memberships_variant_id",
        table_name="catalog_collection_memberships",
    )
    op.drop_index(
        "ix_catalog_collection_memberships_collection_id",
        table_name="catalog_collection_memberships",
    )
    op.drop_table("catalog_collection_memberships")
    op.drop_index("ix_catalog_collections_is_active", table_name="catalog_collections")
    op.drop_index("ix_catalog_collections_type", table_name="catalog_collections")
    op.drop_index("ix_catalog_collections_slug", table_name="catalog_collections")
    op.drop_table("catalog_collections")
    bind = op.get_bind()
    rule_operator.drop(bind, checkfirst=True)
    rule_field.drop(bind, checkfirst=True)
    collection_type.drop(bind, checkfirst=True)

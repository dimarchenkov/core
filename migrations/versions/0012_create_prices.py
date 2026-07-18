"""Create immutable sellable variant price history.

Revision ID: 0012_create_prices
Revises: 0011_add_reversal_movement
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_create_prices"
down_revision: str | None = "0011_add_reversal_movement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create append-only price history and its price type enum."""
    price_type = postgresql.ENUM(
        "retail",
        "promo",
        name="price_type",
        create_type=False,
    )
    price_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "prices",
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("price_type", price_type, nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("amount >= 0", name="ck_prices_amount_nonnegative"),
        sa.CheckConstraint("currency = 'RUB'", name="ck_prices_currency_rub"),
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_prices_variant_id"), "prices", ["variant_id"])
    op.create_index(op.f("ix_prices_price_type"), "prices", ["price_type"])
    op.create_index(op.f("ix_prices_effective_from"), "prices", ["effective_from"])
    op.create_index(
        "ix_prices_variant_type_effective",
        "prices",
        ["variant_id", "price_type", "effective_from"],
    )


def downgrade() -> None:
    """Drop price history before removing its PostgreSQL enum type."""
    op.drop_index("ix_prices_variant_type_effective", table_name="prices")
    op.drop_index(op.f("ix_prices_effective_from"), table_name="prices")
    op.drop_index(op.f("ix_prices_price_type"), table_name="prices")
    op.drop_index(op.f("ix_prices_variant_id"), table_name="prices")
    op.drop_table("prices")
    postgresql.ENUM(name="price_type").drop(op.get_bind(), checkfirst=True)

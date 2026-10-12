"""Move rental tariff snapshots from orders to individual items.

Revision ID: 0040_multi_item_snapshots
Revises: 0039_rental_tariff_snapshot
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_multi_item_snapshots"
down_revision: str | None = "0039_rental_tariff_snapshot"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add item terms, preserve prior one-item drafts, then remove order terms."""
    op.add_column("rental_order_items", sa.Column("rental_tariff_id", sa.Uuid(), nullable=True))
    op.add_column(
        "rental_order_items", sa.Column("rental_tariff_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "rental_order_items",
        sa.Column(
            "timing_source",
            postgresql.ENUM("tariff", "manual", name="rental_timing_source", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("rental_order_items", sa.Column("tariff_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "rental_order_items",
        sa.Column("planned_start_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "rental_order_items", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "rental_order_items",
        sa.Column("recommended_deposit", sa.Numeric(12, 2), nullable=True),
    )
    op.create_foreign_key(
        "fk_rental_order_items_tariff_id_rental_tariffs",
        "rental_order_items",
        "rental_tariffs",
        ["rental_tariff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_rental_order_items_rental_tariff_id",
        "rental_order_items",
        ["rental_tariff_id"],
    )

    op.execute(
        """
        UPDATE rental_order_items AS item
        SET rental_tariff_id = orders.rental_tariff_id,
            rental_tariff_version = orders.rental_tariff_version,
            timing_source = orders.timing_source,
            tariff_snapshot = orders.tariff_snapshot,
            planned_start_at = orders.planned_start_at,
            due_at = orders.planned_return_at,
            recommended_deposit = COALESCE(
                (orders.tariff_snapshot ->> 'deposit_amount')::numeric,
                0
            )
        FROM rental_orders AS orders
        WHERE item.order_id = orders.id
          AND orders.rental_tariff_id IS NOT NULL
          AND 1 = (
              SELECT count(*) FROM rental_order_items AS sibling
              WHERE sibling.order_id = orders.id
          )
        """
    )

    op.drop_index("ix_rental_orders_rental_tariff_id", table_name="rental_orders")
    op.drop_constraint(
        "fk_rental_orders_rental_tariff_id_rental_tariffs",
        "rental_orders",
        type_="foreignkey",
    )
    op.drop_column("rental_orders", "tariff_snapshot")
    op.drop_column("rental_orders", "timing_source")
    op.drop_column("rental_orders", "rental_tariff_version")
    op.drop_column("rental_orders", "rental_tariff_id")


def downgrade() -> None:
    """Restore the former one-snapshot order representation."""
    op.add_column("rental_orders", sa.Column("rental_tariff_id", sa.Uuid(), nullable=True))
    op.add_column("rental_orders", sa.Column("rental_tariff_version", sa.Integer(), nullable=True))
    op.add_column(
        "rental_orders",
        sa.Column(
            "timing_source",
            postgresql.ENUM("tariff", "manual", name="rental_timing_source", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("rental_orders", sa.Column("tariff_snapshot", sa.JSON(), nullable=True))
    op.create_foreign_key(
        "fk_rental_orders_rental_tariff_id_rental_tariffs",
        "rental_orders",
        "rental_tariffs",
        ["rental_tariff_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_rental_orders_rental_tariff_id", "rental_orders", ["rental_tariff_id"])
    op.execute(
        """
        UPDATE rental_orders AS orders
        SET rental_tariff_id = item.rental_tariff_id,
            rental_tariff_version = item.rental_tariff_version,
            timing_source = item.timing_source,
            tariff_snapshot = item.tariff_snapshot
        FROM rental_order_items AS item
        WHERE item.order_id = orders.id
          AND item.rental_tariff_id IS NOT NULL
          AND item.id = (
              SELECT first_item.id FROM rental_order_items AS first_item
              WHERE first_item.order_id = orders.id
              ORDER BY first_item.created_at, first_item.id
              LIMIT 1
          )
        """
    )

    op.drop_index("ix_rental_order_items_rental_tariff_id", table_name="rental_order_items")
    op.drop_constraint(
        "fk_rental_order_items_tariff_id_rental_tariffs",
        "rental_order_items",
        type_="foreignkey",
    )
    op.drop_column("rental_order_items", "recommended_deposit")
    op.drop_column("rental_order_items", "due_at")
    op.drop_column("rental_order_items", "planned_start_at")
    op.drop_column("rental_order_items", "tariff_snapshot")
    op.drop_column("rental_order_items", "timing_source")
    op.drop_column("rental_order_items", "rental_tariff_version")
    op.drop_column("rental_order_items", "rental_tariff_id")

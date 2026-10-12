"""Add READY Rental drafts with immutable tariff snapshots.

Revision ID: 0039_rental_tariff_snapshot
Revises: 0038_rental_tariffs
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_rental_tariff_snapshot"
down_revision: str | None = "0038_rental_tariffs"
branch_labels: str | None = None
depends_on: str | None = None

rental_timing_source = sa.Enum("tariff", "manual", name="rental_timing_source")


def upgrade() -> None:
    """Persist exact tariff provenance, terms, and timing on RentalOrder."""
    op.execute("ALTER TYPE rental_order_status ADD VALUE IF NOT EXISTS 'ready'")
    rental_timing_source.create(op.get_bind(), checkfirst=True)
    op.add_column("rental_orders", sa.Column("rental_tariff_id", sa.Uuid(), nullable=True))
    op.add_column(
        "rental_orders", sa.Column("rental_tariff_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "rental_orders",
        sa.Column(
            "timing_source",
            postgresql.ENUM(
                "tariff", "manual", name="rental_timing_source", create_type=False
            ),
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
    op.create_index(
        "ix_rental_orders_rental_tariff_id",
        "rental_orders",
        ["rental_tariff_id"],
    )


def downgrade() -> None:
    """Remove tariff snapshots and rebuild the order status type without READY."""
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
    rental_timing_source.drop(op.get_bind(), checkfirst=True)

    op.execute("UPDATE rental_orders SET status = 'draft' WHERE status = 'ready'")
    op.execute("ALTER TYPE rental_order_status RENAME TO rental_order_status_old")
    old_status = postgresql.ENUM(
        "draft",
        "issued",
        "closed",
        "cancelled",
        name="rental_order_status",
        create_type=False,
    )
    old_status.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE rental_orders ALTER COLUMN status TYPE rental_order_status "
        "USING status::text::rental_order_status"
    )
    op.execute("DROP TYPE rental_order_status_old")

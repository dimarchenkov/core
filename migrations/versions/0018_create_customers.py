"""Create Customers foundation.

Revision ID: 0018_create_customers
Revises: 0017_create_rental_assets
Create Date: 2026-07-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_create_customers"
down_revision: str | None = "0017_create_rental_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create customers and their stable business-number sequence."""
    op.execute("CREATE SEQUENCE customer_number_seq START WITH 1")
    customer_status = postgresql.ENUM(
        "active",
        "inactive",
        name="customer_status",
        create_type=False,
    )
    customer_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "customers",
        sa.Column("customer_number", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", customer_status, server_default="active", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("updated_by_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_customers_customer_number"),
        "customers",
        ["customer_number"],
        unique=True,
    )
    op.create_index(op.f("ix_customers_full_name"), "customers", ["full_name"])
    op.create_index(op.f("ix_customers_phone"), "customers", ["phone"])
    op.create_index(op.f("ix_customers_status"), "customers", ["status"])


def downgrade() -> None:
    """Drop customers before removing their enum and number sequence."""
    op.drop_index(op.f("ix_customers_status"), table_name="customers")
    op.drop_index(op.f("ix_customers_phone"), table_name="customers")
    op.drop_index(op.f("ix_customers_full_name"), table_name="customers")
    op.drop_index(op.f("ix_customers_customer_number"), table_name="customers")
    op.drop_table("customers")
    postgresql.ENUM(name="customer_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP SEQUENCE customer_number_seq")

"""Add rental asset serial numbers, seal history, and issue/return snapshots.

Revision ID: 0047_rental_asset_identification
Revises: 0046_catalog_collections
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_rental_asset_identification"
down_revision: str | None = "0046_catalog_collections"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Persist optional asset identification and immutable rental snapshots."""
    seal_status = postgresql.ENUM(
        "active", "removed", "broken", name="rental_seal_status"
    )
    seal_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rental_assets", sa.Column("serial_number", sa.String(length=120), nullable=True)
    )
    op.create_index(
        "ix_rental_assets_serial_number", "rental_assets", ["serial_number"], unique=False
    )
    op.create_table(
        "rental_asset_seals",
        sa.Column("rental_asset_id", sa.Uuid(), nullable=False),
        sa.Column("location", sa.String(length=160), nullable=False),
        sa.Column("seal_number", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="rental_seal_status", create_type=False),
            nullable=False,
        ),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["rental_asset_id"], ["rental_assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rental_asset_seals_rental_asset_id",
        "rental_asset_seals",
        ["rental_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_rental_asset_seals_seal_number",
        "rental_asset_seals",
        ["seal_number"],
        unique=False,
    )
    op.create_index(
        "ix_rental_asset_seals_status", "rental_asset_seals", ["status"], unique=False
    )
    op.add_column(
        "rental_order_items",
        sa.Column("asset_identification_snapshot", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rental_order_items",
        sa.Column("seal_verification_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Remove identification snapshots and seal history storage."""
    op.drop_column("rental_order_items", "seal_verification_snapshot")
    op.drop_column("rental_order_items", "asset_identification_snapshot")
    op.drop_index("ix_rental_asset_seals_status", table_name="rental_asset_seals")
    op.drop_index("ix_rental_asset_seals_seal_number", table_name="rental_asset_seals")
    op.drop_index("ix_rental_asset_seals_rental_asset_id", table_name="rental_asset_seals")
    op.drop_table("rental_asset_seals")
    op.drop_index("ix_rental_assets_serial_number", table_name="rental_assets")
    op.drop_column("rental_assets", "serial_number")
    postgresql.ENUM(name="rental_seal_status").drop(op.get_bind(), checkfirst=True)

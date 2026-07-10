"""Create media metadata foundation.

Revision ID: 0005_create_media_foundation
Revises: 0004_create_catalog_variants
Create Date: 2026-07-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_media_foundation"
down_revision: str | None = "0004_create_catalog_variants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create image metadata and polymorphic catalog image-link tables."""
    entity_type = postgresql.ENUM(
        "catalog_product",
        "catalog_variant",
        name="image_link_entity_type",
        create_type=False,
    )
    role = postgresql.ENUM("primary", "gallery", name="image_link_role", create_type=False)
    entity_type.create(op.get_bind(), checkfirst=True)
    role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "images",
        sa.Column("source_key", sa.String(length=1024), nullable=False),
        sa.Column("master_key", sa.String(length=1024), nullable=True),
        sa.Column("web_key", sa.String(length=1024), nullable=True),
        sa.Column("thumb_key", sa.String(length=1024), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=255), nullable=False),
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
        sa.CheckConstraint("size_bytes > 0", name="ck_images_size_bytes_positive"),
        sa.CheckConstraint("width > 0", name="ck_images_width_positive"),
        sa.CheckConstraint("height > 0", name="ck_images_height_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "image_links",
        sa.Column("image_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", entity_type, nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("role", role, nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_image_links_image_id"), "image_links", ["image_id"])
    op.create_index(op.f("ix_image_links_entity_id"), "image_links", ["entity_id"])
    op.create_index(
        "uq_image_links_active_primary_entity",
        "image_links",
        ["entity_type", "entity_id"],
        unique=True,
        postgresql_where=sa.text("role = 'primary' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop media metadata tables and PostgreSQL enum types."""
    op.drop_index("uq_image_links_active_primary_entity", table_name="image_links")
    op.drop_index(op.f("ix_image_links_entity_id"), table_name="image_links")
    op.drop_index(op.f("ix_image_links_image_id"), table_name="image_links")
    op.drop_table("image_links")
    op.drop_table("images")
    postgresql.ENUM(name="image_link_role").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="image_link_entity_type").drop(op.get_bind(), checkfirst=True)

"""Create external publication state and attributed attempt history.

Revision ID: 0014_create_publications
Revises: 0013_generate_barcodes
Create Date: 2026-07-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_create_publications"
down_revision: str | None = "0013_generate_barcodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create current publication projections and their audited attempts."""
    publication_channel = postgresql.ENUM(
        "aqsi",
        name="publication_channel",
        create_type=False,
    )
    publication_status = postgresql.ENUM(
        "pending",
        "accepted",
        "published",
        "failed",
        "disabled",
        name="publication_status",
        create_type=False,
    )
    publication_attempt_status = postgresql.ENUM(
        "pending",
        "processing",
        "accepted",
        "published",
        "failed",
        name="publication_attempt_status",
        create_type=False,
    )
    publication_operation = postgresql.ENUM(
        "create",
        "update",
        name="publication_operation",
        create_type=False,
    )
    for enum_type in (
        publication_channel,
        publication_status,
        publication_attempt_status,
        publication_operation,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "publications",
        sa.Column("variant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", publication_channel, nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("status", publication_status, server_default="pending", nullable=False),
        sa.Column("last_requested_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("last_verified_payload_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["variant_id"], ["catalog_variants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "variant_id", name="uq_publications_channel_variant"),
    )
    op.create_index(op.f("ix_publications_variant_id"), "publications", ["variant_id"])
    op.create_index(op.f("ix_publications_channel"), "publications", ["channel"])

    op.create_table(
        "publication_attempts",
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("operation", publication_operation, nullable=False),
        sa.Column("status", publication_attempt_status, server_default="pending", nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "attempt_number",
            name="uq_publication_attempts_number",
        ),
    )
    op.create_index(
        op.f("ix_publication_attempts_publication_id"),
        "publication_attempts",
        ["publication_id"],
    )
    op.create_index(
        "ix_publication_attempts_publication_requested",
        "publication_attempts",
        ["publication_id", "requested_at"],
    )


def downgrade() -> None:
    """Drop publication history before removing its PostgreSQL enum types."""
    op.drop_index(
        "ix_publication_attempts_publication_requested",
        table_name="publication_attempts",
    )
    op.drop_index(
        op.f("ix_publication_attempts_publication_id"),
        table_name="publication_attempts",
    )
    op.drop_table("publication_attempts")
    op.drop_index(op.f("ix_publications_channel"), table_name="publications")
    op.drop_index(op.f("ix_publications_variant_id"), table_name="publications")
    op.drop_table("publications")
    for enum_name in (
        "publication_operation",
        "publication_attempt_status",
        "publication_status",
        "publication_channel",
    ):
        postgresql.ENUM(name=enum_name).drop(op.get_bind(), checkfirst=True)

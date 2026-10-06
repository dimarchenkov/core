"""Add generic integrations and encrypted credentials.

Revision ID: 0032_integration_settings
Revises: 0031_test_data_scope
Create Date: 2026-10-06 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_integration_settings"
down_revision: str | None = "0031_test_data_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

integration_provider = postgresql.ENUM(
    "aqsi", name="integration_provider", create_type=False
)
credential_kind = postgresql.ENUM(
    "api_key", name="integration_credential_kind", create_type=False
)


def _base_columns() -> list[sa.Column[object]]:
    """Return shared entity columns used by both new tables."""
    return [
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
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    """Create non-singleton integrations and authenticated-encrypted credentials."""
    integration_provider.create(op.get_bind(), checkfirst=True)
    credential_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "integrations",
        *_base_columns(),
        sa.Column("provider", integration_provider, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("configuration", sa.JSON(), server_default="{}", nullable=False),
    )
    op.create_index("ix_integrations_provider", "integrations", ["provider"], unique=False)
    op.create_table(
        "integration_credentials",
        *_base_columns(),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("kind", credential_kind, nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("encryption_algorithm", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "integration_id",
            "kind",
            name="uq_integration_credentials_integration_kind",
        ),
    )
    op.create_index(
        "ix_integration_credentials_integration_id",
        "integration_credentials",
        ["integration_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove integration settings tables and their enum types."""
    op.drop_index(
        "ix_integration_credentials_integration_id",
        table_name="integration_credentials",
    )
    op.drop_table("integration_credentials")
    op.drop_index("ix_integrations_provider", table_name="integrations")
    op.drop_table("integrations")
    postgresql.ENUM(name="integration_credential_kind").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="integration_provider").drop(op.get_bind(), checkfirst=True)

"""Add immutable Rental agreement templates, settings, and PDF artifacts.

Revision ID: 0043_rental_agreements
Revises: 0042_rental_item_issue_audit
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0043_rental_agreements"
down_revision: str | None = "0042_rental_item_issue_audit"
branch_labels: str | None = None
depends_on: str | None = None


def _base_columns() -> list[sa.Column]:
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


def _tracking_constraints(table: str) -> None:
    for column in ("deleted_by_id", "created_by_id", "updated_by_id"):
        op.create_foreign_key(
            f"fk_{table}_{column}_users", table, "users", [column], ["id"], ondelete="SET NULL"
        )


def upgrade() -> None:
    """Create append-only agreement persistence and a concurrency-safe number sequence."""
    op.create_table(
        "rental_agreement_settings",
        *_base_columns(),
        sa.Column("settings_key", sa.String(32), nullable=False),
        sa.Column("lessor_name", sa.String(255), server_default="", nullable=False),
        sa.Column("lessor_inn", sa.String(32), server_default="", nullable=False),
        sa.Column("lessor_ogrnip", sa.String(32), server_default="", nullable=False),
        sa.Column("agreement_city", sa.String(128), server_default="Москва", nullable=False),
        sa.Column("lessor_address", sa.String(500), server_default="", nullable=False),
        sa.Column("lessor_phone", sa.String(64), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("settings_key"),
    )
    _tracking_constraints("rental_agreement_settings")
    op.create_table(
        "rental_agreement_template_versions",
        *_base_columns(),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("markdown_source", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_version"),
    )
    _tracking_constraints("rental_agreement_template_versions")
    op.create_index(
        "ix_rental_agreement_template_versions_template_version",
        "rental_agreement_template_versions",
        ["template_version"],
    )
    op.create_table(
        "rental_agreements",
        *_base_columns(),
        sa.Column("rental_order_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("agreement_number", sa.String(32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rendered_markdown", sa.Text(), nullable=False),
        sa.Column("rendered_html", sa.Text(), nullable=False),
        sa.Column("lessor_snapshot", sa.JSON(), nullable=False),
        sa.Column("items_snapshot", sa.JSON(), nullable=False),
        sa.Column("pdf_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column(
            "attestation_method", sa.String(32), server_default="internal_hash", nullable=False
        ),
        sa.Column("status", sa.String(32), server_default="generated", nullable=False),
        sa.ForeignKeyConstraint(["rental_order_id"], ["rental_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["template_version_id"], ["rental_agreement_template_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agreement_number"),
        sa.UniqueConstraint("rental_order_id"),
    )
    _tracking_constraints("rental_agreements")
    op.create_index(
        "ix_rental_agreements_rental_order_id", "rental_agreements", ["rental_order_id"]
    )
    op.create_index(
        "ix_rental_agreements_template_version_id",
        "rental_agreements",
        ["template_version_id"],
    )
    op.create_index(
        "ix_rental_agreements_agreement_number",
        "rental_agreements",
        ["agreement_number"],
    )
    op.execute("CREATE SEQUENCE rental_agreement_number_seq START WITH 1")


def downgrade() -> None:
    """Remove agreement storage and numbering infrastructure."""
    op.execute("DROP SEQUENCE IF EXISTS rental_agreement_number_seq")
    op.drop_table("rental_agreements")
    op.drop_table("rental_agreement_template_versions")
    op.drop_table("rental_agreement_settings")

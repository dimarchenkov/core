"""Add immutable item return calculations and inspection outcomes.

Revision ID: 0044_rental_return_overdue
Revises: 0043_rental_agreements
Create Date: 2026-10-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_rental_return_overdue"
down_revision: str | None = "0043_rental_agreements"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Store authoritative return facts without recalculating history from live tariffs."""
    return_condition = postgresql.ENUM(
        "good", "damaged", "incomplete", "lost", name="rental_return_condition"
    )
    overdue_state = postgresql.ENUM(
        "on_time",
        "overdue_grace",
        "overdue_chargeable",
        name="rental_overdue_state",
    )
    return_condition.create(op.get_bind(), checkfirst=True)
    overdue_state.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "rental_order_items",
        sa.Column(
            "return_condition",
            postgresql.ENUM(name="rental_return_condition", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "rental_order_items",
        sa.Column(
            "overdue_state",
            postgresql.ENUM(name="rental_overdue_state", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("rental_order_items", sa.Column("overdue_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "rental_order_items", sa.Column("overdue_charge", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "rental_order_items", sa.Column("damage_charge", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "rental_order_items", sa.Column("deposit_applied", sa.Numeric(12, 2), nullable=True)
    )
    op.add_column(
        "rental_order_items", sa.Column("return_calculation_snapshot", sa.JSON(), nullable=True)
    )
    op.create_check_constraint(
        "ck_rental_order_items_overdue_minutes_nonnegative",
        "rental_order_items",
        "overdue_minutes IS NULL OR overdue_minutes >= 0",
    )
    for column in ("overdue_charge", "damage_charge", "deposit_applied"):
        op.create_check_constraint(
            f"ck_rental_order_items_{column}_nonnegative",
            "rental_order_items",
            f"{column} IS NULL OR {column} >= 0",
        )


def downgrade() -> None:
    """Remove Sprint 4.18 return facts and their enum types."""
    for column in ("deposit_applied", "damage_charge", "overdue_charge"):
        op.drop_constraint(
            f"ck_rental_order_items_{column}_nonnegative",
            "rental_order_items",
            type_="check",
        )
    op.drop_constraint(
        "ck_rental_order_items_overdue_minutes_nonnegative",
        "rental_order_items",
        type_="check",
    )
    for column in (
        "return_calculation_snapshot",
        "deposit_applied",
        "damage_charge",
        "overdue_charge",
        "overdue_minutes",
        "overdue_state",
        "return_condition",
    ):
        op.drop_column("rental_order_items", column)
    postgresql.ENUM(name="rental_overdue_state").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="rental_return_condition").drop(op.get_bind(), checkfirst=True)

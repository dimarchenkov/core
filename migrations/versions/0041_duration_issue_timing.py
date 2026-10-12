"""Separate planned rental date from actual item issue timing.

Revision ID: 0041_duration_issue_timing
Revises: 0040_multi_item_snapshots
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0041_duration_issue_timing"
down_revision: str | None = "0040_multi_item_snapshots"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Persist planned dates and defer DURATION due calculation until issue."""
    op.add_column("rental_order_items", sa.Column("planned_rental_date", sa.Date(), nullable=True))
    op.add_column(
        "rental_order_items",
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE rental_order_items AS item
        SET planned_rental_date = (item.planned_start_at AT TIME ZONE 'Europe/Moscow')::date,
            issued_at = orders.issued_at
        FROM rental_orders AS orders
        WHERE orders.id = item.order_id
          AND item.rental_tariff_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE rental_order_items
        SET planned_start_at = NULL,
            due_at = CASE
                WHEN issued_at IS NULL THEN NULL
                ELSE issued_at
                    + ((tariff_snapshot ->> 'duration_minutes')::integer * interval '1 minute')
            END,
            tariff_snapshot = jsonb_set(
                jsonb_set(
                    jsonb_set(
                        tariff_snapshot::jsonb,
                        '{planned_rental_date}',
                        to_jsonb(planned_rental_date::text),
                        true
                    ),
                    '{planned_start_at}',
                    'null'::jsonb,
                    true
                ),
                '{due_at}',
                'null'::jsonb,
                true
            )::json
        WHERE timing_source = 'tariff'
          AND tariff_snapshot ->> 'tariff_type' = 'duration'
        """
    )
    op.execute(
        """
        UPDATE rental_order_items
        SET tariff_snapshot = jsonb_set(
            tariff_snapshot::jsonb,
            '{planned_rental_date}',
            to_jsonb(planned_rental_date::text),
            true
        )::json
        WHERE rental_tariff_id IS NOT NULL
          AND NOT (
              timing_source = 'tariff'
              AND tariff_snapshot ->> 'tariff_type' = 'duration'
          )
        """
    )


def downgrade() -> None:
    """Reconstruct a deterministic midnight placeholder for the former model."""
    op.execute(
        """
        UPDATE rental_order_items
        SET planned_start_at = planned_rental_date::timestamp AT TIME ZONE 'Europe/Moscow',
            due_at = (
                planned_rental_date::timestamp AT TIME ZONE 'Europe/Moscow'
                + ((tariff_snapshot ->> 'duration_minutes')::integer * interval '1 minute')
            )
        WHERE planned_rental_date IS NOT NULL
          AND timing_source = 'tariff'
          AND tariff_snapshot ->> 'tariff_type' = 'duration'
          AND planned_start_at IS NULL
        """
    )
    op.drop_column("rental_order_items", "issued_at")
    op.drop_column("rental_order_items", "planned_rental_date")

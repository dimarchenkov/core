"""Create append-only operational activity events.

Revision ID: 0016_create_activity_events
Revises: 0015_create_intake_sessions
Create Date: 2026-07-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_create_activity_events"
down_revision: str | None = "0015_create_intake_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the operational event stream used by employee activity feeds."""
    event_type = postgresql.ENUM(
        "intake.session_started",
        "intake.item_added",
        "intake.item_abandoned",
        "intake.session_completed",
        "intake.session_abandoned",
        name="activity_event_type",
        create_type=False,
    )
    entity_type = postgresql.ENUM(
        "intake_session",
        "intake_item",
        name="activity_entity_type",
        create_type=False,
    )
    event_type.create(op.get_bind(), checkfirst=True)
    entity_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "activity_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", entity_type, nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("data", sa.JSON(), server_default="{}", nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activity_events_event_type"), "activity_events", ["event_type"])
    op.create_index(op.f("ix_activity_events_actor_id"), "activity_events", ["actor_id"])
    op.create_index(op.f("ix_activity_events_entity_id"), "activity_events", ["entity_id"])
    op.create_index(op.f("ix_activity_events_occurred_at"), "activity_events", ["occurred_at"])


def downgrade() -> None:
    """Remove operational events and their bounded enum types."""
    op.drop_index(op.f("ix_activity_events_occurred_at"), table_name="activity_events")
    op.drop_index(op.f("ix_activity_events_entity_id"), table_name="activity_events")
    op.drop_index(op.f("ix_activity_events_actor_id"), table_name="activity_events")
    op.drop_index(op.f("ix_activity_events_event_type"), table_name="activity_events")
    op.drop_table("activity_events")
    postgresql.ENUM(name="activity_entity_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="activity_event_type").drop(op.get_bind(), checkfirst=True)

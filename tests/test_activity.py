from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.activity.enums import ActivityEntityType, ActivityEventType
from core.activity.models import ActivityEvent
from core.activity.service import ActivityEventService
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.main import create_app
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide the two tables required by the operational activity context."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, ActivityEvent.__table__])
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as session:
        yield session


def test_activity_event_participates_in_callers_transaction(session: Session) -> None:
    """The event service cannot commit a success fact independently."""
    actor = User(email="actor@example.com", full_name="Actor", password_hash="unused")
    session.add(actor)
    session.commit()

    ActivityEventService(session).record_intake_session_started(
        session_id=generate_uuid_v7(),
        actor_id=actor.id,
    )
    session.rollback()

    assert session.scalars(select(ActivityEvent)).all() == []


def test_activity_event_has_no_mutation_or_soft_delete_contract() -> None:
    """Operational outcomes expose no domain API for editing or deletion."""
    event = ActivityEvent(
        event_type=ActivityEventType.INTAKE_SESSION_STARTED,
        actor_id=generate_uuid_v7(),
        entity_type=ActivityEntityType.INTAKE_SESSION,
        entity_id=generate_uuid_v7(),
        data={},
    )

    assert not hasattr(event, "soft_delete")
    assert not hasattr(event, "updated_at")


def test_employee_feed_is_owned_filtered_and_paginated(session: Session) -> None:
    """An employee sees only their newest meaningful outcomes."""
    first = User(email="first@example.com", full_name="First", password_hash="unused")
    second = User(email="second@example.com", full_name="Second", password_hash="unused")
    session.add_all([first, second])
    session.commit()
    now = datetime.now(UTC)
    activity = ActivityEventService(session)
    started = activity.record_intake_session_started(
        session_id=generate_uuid_v7(),
        actor_id=first.id,
    )
    started.occurred_at = now
    activity.record_intake_session_completed(
        session_id=generate_uuid_v7(),
        receipt_id=generate_uuid_v7(),
        item_count=2,
        total_quantity=5,
        duration_seconds=90,
        actor_id=first.id,
        occurred_at=now + timedelta(seconds=1),
    )
    activity.record_intake_session_started(
        session_id=generate_uuid_v7(),
        actor_id=second.id,
    )
    session.commit()

    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_current_user] = lambda: first
    with TestClient(app) as client:
        page = client.get("/api/activity/me?limit=1&offset=0")
        filtered = client.get(
            "/api/activity/me?event_type=intake.session_started&limit=50&offset=0"
        )
    app.dependency_overrides.clear()

    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["items"][0]["event_type"] == "intake.session_completed"
    assert page.json()["items"][0]["data"]["duration_seconds"] == 90
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["actor_id"] == str(first.id)

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Literal

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.orm import Session

from core.config import get_settings
from core.intake.models import IntakeSession
from core.shared.db import UUIDv7

logger = logging.getLogger(__name__)
_PENDING_EVENTS_KEY = "intake_realtime_events"

IntakeEventType = Literal[
    "intake.changed",
    "intake.finalized",
    "intake.cancelled",
    "intake.deleted",
]


class IntakeVersionConflictError(Exception):
    """Raised when an absolute write was based on an older Intake projection."""

    def __init__(self, current_version: int) -> None:
        """Retain the authoritative version for a useful conflict response."""
        self.current_version = current_version
        super().__init__("Intake changed on another device.")


def ensure_expected_version(intake: IntakeSession, expected_version: int | None) -> None:
    """Reject stale absolute edits while retaining compatibility with older clients."""
    if expected_version is not None and intake.version != expected_version:
        logger.info(
            "Rejected stale Intake write",
            extra={
                "intake_id": str(intake.id),
                "expected_version": expected_version,
                "current_version": intake.version,
            },
        )
        raise IntakeVersionConflictError(intake.version)


def commit_intake_change(
    session: Session,
    intake: IntakeSession,
    *,
    event_type: IntakeEventType = "intake.changed",
) -> int:
    """Increment, commit, then publish one small authoritative-refetch notification."""
    intake.version += 1
    version = intake.version
    stage_intake_event(session, intake.id, version, event_type=event_type)
    session.commit()
    logger.info(
        "Committed Intake version change",
        extra={"intake_id": str(intake.id), "intake_version": version, "event_type": event_type},
    )
    return version


def stage_intake_event(
    session: Session,
    intake_id: UUIDv7,
    version: int,
    *,
    event_type: IntakeEventType,
) -> None:
    """Coalesce one transaction's notifications until SQLAlchemy confirms commit."""
    pending: dict[UUIDv7, tuple[int, IntakeEventType]] = session.info.setdefault(
        _PENDING_EVENTS_KEY,
        {},
    )
    previous = pending.get(intake_id)
    if previous is None or version >= previous[0]:
        pending[intake_id] = (version, event_type)


@event.listens_for(Session, "after_commit")
def _publish_committed_intake_events(session: Session) -> None:
    """Publish staged notifications only when the database transaction is durable."""
    pending = session.info.pop(_PENDING_EVENTS_KEY, {})
    for intake_id, (version, event_type) in pending.items():
        publish_intake_event(intake_id, version, event_type=event_type)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_intake_events(session: Session) -> None:
    """Never leak a notification for state that did not commit."""
    session.info.pop(_PENDING_EVENTS_KEY, None)


def publish_intake_event(
    intake_id: UUIDv7,
    version: int,
    *,
    event_type: IntakeEventType,
) -> None:
    """Publish after commit; transport failure never invalidates the durable mutation."""
    settings = get_settings()
    if settings.env == "test":
        return
    payload = json.dumps(
        {
            "intake_id": str(intake_id),
            "version": version,
            "event_type": event_type,
        },
        separators=(",", ":"),
    )
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        client.publish(_channel(intake_id), payload)
        logger.info(
            "Published Intake realtime event",
            extra={
                "intake_id": str(intake_id),
                "intake_version": version,
                "event_type": event_type,
            },
        )
    except (RedisError, OSError):
        logger.warning(
            "Could not publish Intake realtime event; clients will recover by polling",
            extra={"intake_id": str(intake_id), "intake_version": version},
            exc_info=True,
        )
    finally:
        if client is not None:
            client.close()


async def stream_intake_events(intake_id: UUIDv7) -> AsyncIterator[str]:
    """Stream one Intake's Redis notifications with proxy-safe heartbeats."""
    settings = get_settings()
    client = AsyncRedis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=None,
    )
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(_channel(intake_id))
        reconnect_deadline = asyncio.get_running_loop().time() + 300
        logger.info("Intake SSE subscribed", extra={"intake_id": str(intake_id)})
        yield "retry: 3000\n\n"
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
            if asyncio.get_running_loop().time() >= reconnect_deadline:
                return
            if message is None:
                yield ": heartbeat\n\n"
                continue
            data = message.get("data")
            if not isinstance(data, str):
                continue
            try:
                event_type = json.loads(data).get("event_type", "intake.changed")
            except (json.JSONDecodeError, AttributeError):
                continue
            yield f"event: {event_type}\ndata: {data}\n\n"
    except (RedisError, OSError):
        logger.warning(
            "Intake SSE disconnected from Redis",
            extra={"intake_id": str(intake_id)},
            exc_info=True,
        )
    finally:
        await pubsub.aclose()
        await client.aclose()
        logger.info("Intake SSE disconnected", extra={"intake_id": str(intake_id)})


def _channel(intake_id: UUIDv7) -> str:
    """Scope fanout to exactly one open Intake."""
    return f"core:intake:{intake_id}"

from __future__ import annotations

from uuid import UUID

import core.identity.models  # noqa: F401  # Register User FKs in the worker process.
from core.config import get_settings
from core.database import SessionLocal
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.aqsi.processor import AqsiPublicationProcessor


def publish_aqsi_attempt(attempt_id: str) -> None:
    """RQ entry point that processes one persisted AQSI attempt."""
    settings = get_settings()
    with SessionLocal() as session, AqsiHttpClient(settings) as gateway:
        AqsiPublicationProcessor(session, settings, gateway).process(UUID(attempt_id))

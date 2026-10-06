from __future__ import annotations

import logging
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Repeat, Retry
from rq.job import JobStatus

import core.identity.models  # noqa: F401  # Register User FKs in the worker process.
from core.config import get_settings
from core.database import SessionLocal
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.aqsi.processor import AqsiPublicationProcessor
from core.integrations.aqsi.service import (
    AqsiIntegrationDisabledError,
    AqsiIntegrationNotConfiguredError,
    AqsiPublicationService,
)
from core.integrations.aqsi.synchronization import AqsiCatalogSynchronizationService
from core.integrations.credentials import CredentialDecryptionError
from core.integrations.master_key import MasterEncryptionKeyError
from core.integrations.runtime import (
    AmbiguousIntegrationError,
    get_aqsi_integration,
    resolve_aqsi_settings,
)
from core.jobs import DEFAULT_QUEUE_NAME, get_default_queue

logger = logging.getLogger(__name__)

AQSI_CATALOG_SYNC_INTERVAL_SECONDS = 300
AQSI_CATALOG_SYNC_REPEATS = 1_000_000
AQSI_CATALOG_SYNC_SCHEDULE_JOB_ID = "core-aqsi-catalog-sync"


def publish_aqsi_attempt(attempt_id: str) -> None:
    """RQ entry point that processes one persisted AQSI attempt."""
    settings = get_settings()
    with SessionLocal() as session:
        runtime_settings, _ = resolve_aqsi_settings(session, settings)
        with AqsiHttpClient(runtime_settings) as gateway:
            AqsiPublicationProcessor(session, runtime_settings, gateway).process(UUID(attempt_id))


def enqueue_aqsi_attempt(queue: Queue, attempt_id: UUID) -> None:
    """Enqueue one persisted publication attempt with the standard bounded retry policy."""
    queue.enqueue(
        publish_aqsi_attempt,
        str(attempt_id),
        job_timeout=120,
        retry=Retry(max=3, interval=[2, 10, 30]),
    )


def synchronize_aqsi_catalog(
    actor_id: str | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    """Queue new and locally outdated ready Variants for the configured AQSI projection."""
    settings = get_settings()
    with SessionLocal() as session:
        try:
            integration = get_aqsi_integration(session)
            if integration is None:
                return {"status": "not_configured", "examined": 0, "queued": 0}
            if not integration.enabled:
                return {"status": "disabled", "examined": 0, "queued": 0}
            if not force and integration.configuration.get("catalog_sync_enabled") is not True:
                return {"status": "automatic_sync_disabled", "examined": 0, "queued": 0}

            runtime_settings, _ = resolve_aqsi_settings(session, settings)
            plan = AqsiCatalogSynchronizationService(session, runtime_settings).plan(
                actor_id=UUID(actor_id) if actor_id is not None else None,
                retry_unchanged_failures=force,
            )
        except (
            AmbiguousIntegrationError,
            AqsiIntegrationDisabledError,
            AqsiIntegrationNotConfiguredError,
            CredentialDecryptionError,
            MasterEncryptionKeyError,
        ) as exc:
            logger.warning(
                "AQSI catalog synchronization skipped because configuration is unavailable",
                extra={"error_type": type(exc).__name__},
            )
            return {"status": "configuration_unavailable", "examined": 0, "queued": 0}

        queue = get_default_queue()
        failed_to_enqueue = 0
        publication_service = AqsiPublicationService(session, runtime_settings)
        for attempt_id in plan.attempt_ids:
            try:
                enqueue_aqsi_attempt(queue, attempt_id)
            except RedisError:
                failed_to_enqueue += 1
                publication_service.mark_enqueue_failed(attempt_id)

        logger.info(
            "AQSI catalog synchronization sweep completed",
            extra={
                "examined": plan.examined,
                "queued": plan.queued - failed_to_enqueue,
                "not_ready": plan.skipped_not_ready,
                "unchanged": plan.unchanged,
                "queue_failures": failed_to_enqueue,
            },
        )
        return {
            "status": "completed",
            "examined": plan.examined,
            "queued": plan.queued - failed_to_enqueue,
        }


def ensure_aqsi_catalog_sync_schedule(redis_connection: Redis) -> bool:
    """Register one durable five-minute AQSI sweep consumed by the RQ scheduler."""
    queue = Queue(DEFAULT_QUEUE_NAME, connection=redis_connection)
    existing = queue.fetch_job(AQSI_CATALOG_SYNC_SCHEDULE_JOB_ID)
    active_statuses = {
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
    if existing is not None and existing.get_status(refresh=True) in active_statuses:
        return False
    if existing is not None:
        existing.delete()
    queue.enqueue(
        synchronize_aqsi_catalog,
        job_id=AQSI_CATALOG_SYNC_SCHEDULE_JOB_ID,
        job_timeout=300,
        repeat=Repeat(
            times=AQSI_CATALOG_SYNC_REPEATS,
            interval=AQSI_CATALOG_SYNC_INTERVAL_SECONDS,
        ),
    )
    return True

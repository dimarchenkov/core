from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Repeat, Retry
from rq.job import JobStatus

import core.identity.models  # noqa: F401  # Register User FKs in the worker process.
from core.config import get_settings
from core.database import SessionLocal
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.integrations.aqsi.models import AqsiCatalogReconciliationRun
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
) -> dict[str, int | str | bool]:
    """Compare actual AQSI state and queue safe create/update repairs."""
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
            actor_uuid = UUID(actor_id) if actor_id is not None else None
            run = AqsiCatalogReconciliationRun(
                integration_id=integration.id,
                trigger="manual" if force or actor_id is not None else "periodic",
                status="running",
                started_at=datetime.now(UTC),
                created_by_id=actor_uuid,
            )
            session.add(run)
            session.commit()
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

        try:
            with AqsiHttpClient(runtime_settings) as gateway:
                plan = AqsiCatalogSynchronizationService(session, runtime_settings).plan(
                    actor_id=actor_uuid,
                    retry_unchanged_failures=force,
                    gateway=gateway,
                )
        except AqsiApiError as exc:
            run.status = "failed"
            run.error_code = exc.code[:128]
            run.error_message = str(exc)[:1000]
            run.completed_at = datetime.now(UTC)
            session.commit()
            logger.warning(
                "AQSI catalog reconciliation failed during remote comparison",
                extra={
                    "reconciliation_run_id": str(run.id),
                    "error_code": exc.code,
                },
            )
            return {
                "status": "failed",
                "examined": 0,
                "queued": 0,
                "error_code": exc.code,
            }
        except Exception:
            run.status = "failed"
            run.error_code = "internal_error"
            run.error_message = "Core could not complete AQSI catalog reconciliation."
            run.completed_at = datetime.now(UTC)
            session.commit()
            logger.exception(
                "AQSI catalog reconciliation failed unexpectedly",
                extra={"reconciliation_run_id": str(run.id)},
            )
            raise

        queue = get_default_queue()
        failed_to_enqueue = 0
        publication_service = AqsiPublicationService(session, runtime_settings)
        for attempt_id in plan.attempt_ids:
            try:
                enqueue_aqsi_attempt(queue, attempt_id)
            except RedisError:
                failed_to_enqueue += 1
                publication_service.mark_enqueue_failed(attempt_id)

        run.status = "needs_attention" if failed_to_enqueue else plan.status
        run.completed_at = datetime.now(UTC)
        run.expected_count = plan.expected
        run.skipped_not_ready_count = plan.skipped_not_ready
        run.remote_total_count = plan.remote_total
        run.remote_managed_count = plan.remote_managed
        run.unmanaged_remote_count = plan.unmanaged_remote
        run.missing_count = plan.missing_count
        run.mismatched_count = plan.mismatched_count
        run.deletion_candidate_count = plan.deletion_candidate_count
        run.queued_count = plan.queued - failed_to_enqueue
        run.queue_failure_count = failed_to_enqueue
        run.deletion_guard_triggered = plan.deletion_guard_triggered
        run.destructive_actions_blocked = plan.destructive_actions_blocked
        run.missing_external_ids = list(plan.missing_external_ids)
        run.mismatched_external_ids = list(plan.mismatched_external_ids)
        run.deletion_candidate_external_ids = list(plan.deletion_candidate_external_ids)
        if failed_to_enqueue:
            run.error_code = "queue_error"
            run.error_message = "One or more AQSI repair jobs could not be queued."
        session.commit()

        logger.info(
            "AQSI catalog reconciliation completed",
            extra={
                "reconciliation_run_id": str(run.id),
                "status": run.status,
                "examined": plan.examined,
                "expected": plan.expected,
                "remote_total": plan.remote_total,
                "remote_managed": plan.remote_managed,
                "missing": plan.missing_count,
                "mismatched": plan.mismatched_count,
                "deletion_candidates": plan.deletion_candidate_count,
                "deletion_guard_triggered": plan.deletion_guard_triggered,
                "queued": plan.queued - failed_to_enqueue,
                "not_ready": plan.skipped_not_ready,
                "unchanged": plan.unchanged,
                "queue_failures": failed_to_enqueue,
            },
        )
        return {
            "status": run.status,
            "examined": plan.examined,
            "expected": plan.expected,
            "remote_total": plan.remote_total,
            "remote_managed": plan.remote_managed,
            "missing": plan.missing_count,
            "mismatched": plan.mismatched_count,
            "deletion_candidates": plan.deletion_candidate_count,
            "deletion_guard_triggered": plan.deletion_guard_triggered,
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

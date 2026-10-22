from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter, sleep
from uuid import UUID

from redis.exceptions import RedisError
from rq import Queue, Repeat
from rq.job import JobStatus
from sqlalchemy import or_, select

import core.identity.models  # noqa: F401
from core.config import get_settings
from core.database import SessionLocal
from core.integrations.checkout import CheckoutProviderFactory
from core.jobs import CHECKOUT_QUEUE_NAME, get_checkout_queue
from core.sales.checkout import CheckoutService
from core.sales.enums import FiscalizationJobStatus, FiscalizationStatus, SaleStatus
from core.sales.models import FiscalizationJob

logger = logging.getLogger(__name__)

FISCALIZATION_RECOVERY_INTERVAL_SECONDS = 15
FISCALIZATION_RECOVERY_REPEATS = 1_000_000
FISCALIZATION_RECOVERY_JOB_ID = "core-fiscalization-recovery-checkout"
LEGACY_FISCALIZATION_RECOVERY_JOB_ID = "core-fiscalization-recovery"
FISCALIZATION_STALE_AFTER = timedelta(minutes=2)
FISCALIZATION_POLL_DELAYS = (0.0, 0.5, 0.5, 0.75, 0.75, 1.0, 1.0, 1.5, 2.0)
MAX_UNKNOWN_QUERY_RUNS = 3
TEMPORARY_ERROR_CODES = {
    "connect_timeout",
    "connect_error",
    "timeout",
    "network_error",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
    "unknown_provider_status",
}


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite-naive test values and PostgreSQL-aware timestamps."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enqueue_fiscalization_job(queue: Queue, job_id: UUID | str, *, delay: int = 0) -> None:
    """Wake one DB-backed fiscal job; the database remains authoritative."""
    kwargs = {"job_timeout": 90, "result_ttl": 300, "failure_ttl": 86400}
    if delay:
        queue.enqueue_in(timedelta(seconds=delay), run_fiscalization_job, str(job_id), **kwargs)
    else:
        queue.enqueue(run_fiscalization_job, str(job_id), **kwargs)


def enqueue_sale_fiscalization(sale_id: UUID | str) -> bool:
    """Best-effort immediate wakeup for a durable job created by checkout."""
    with SessionLocal() as session:
        job_id = session.scalar(
            select(FiscalizationJob.id).where(
                FiscalizationJob.sale_id == UUID(str(sale_id)),
                FiscalizationJob.status == FiscalizationJobStatus.QUEUED,
            )
        )
    if job_id is None:
        return False
    try:
        enqueue_fiscalization_job(get_checkout_queue(), job_id)
    except RedisError:
        logger.exception("Fiscalization wakeup failed; recovery sweep will retry sale=%s", sale_id)
        return False
    return True


def run_fiscalization_job(job_id: str) -> str:
    """Claim and advance one fiscalization independently from any browser request."""
    started = perf_counter()
    claimed = _claim_job(UUID(job_id))
    if claimed is None:
        return "not_claimed"
    sale_id, owner_id, integration_id, attempt, operation_id = claimed
    final = "queued"
    final_error: str | None = None
    manual_review = False
    try:
        with SessionLocal() as session:
            binding = CheckoutProviderFactory(session, get_settings()).for_recovery(integration_id)
            try:
                checkout = CheckoutService(session, binding)
                for delay in FISCALIZATION_POLL_DELAYS:
                    if delay:
                        sleep(delay)
                    sale = checkout.process_fiscalization(sale_id, owner_id=owner_id)
                    fiscal = sale.fiscalization
                    if fiscal is None:
                        raise RuntimeError("Fiscalization disappeared while processing its job.")
                    operation_id = fiscal.external_id
                    _sync_operation(job_id, operation_id)
                    if fiscal.status is FiscalizationStatus.SUCCEEDED:
                        final = "completed"
                        break
                    if fiscal.status is FiscalizationStatus.FAILED:
                        final_error = fiscal.error_message or fiscal.error_code
                        final = "queued" if fiscal.error_code in TEMPORARY_ERROR_CODES else "failed"
                        break
                    if fiscal.status is FiscalizationStatus.UNKNOWN and (
                        not fiscal.external_id or attempt >= MAX_UNKNOWN_QUERY_RUNS
                    ):
                        final = "failed"
                        final_error = (
                            fiscal.error_message or "Fiscalization outcome requires manual review."
                        )
                        manual_review = True
                        break
            finally:
                if binding.close is not None:
                    binding.close()
    except Exception as exc:
        logger.exception("Fiscalization worker failed sale=%s job=%s", sale_id, job_id)
        final_error = str(exc)
        manual_review = operation_id is None
        if manual_review:
            _quarantine_job(UUID(job_id), final_error)
            return "failed"
        _finish_job(UUID(job_id), "queued", final_error)
        try:
            enqueue_fiscalization_job(get_checkout_queue(), job_id, delay=5)
        except RedisError:
            logger.warning("Fiscalization retry wakeup failed; sweep will recover job=%s", job_id)
        return "queued"

    if manual_review:
        _quarantine_job(
            UUID(job_id), final_error or "Fiscalization outcome requires manual review."
        )
    else:
        _finish_job(UUID(job_id), final, final_error)
    if final == "queued":
        try:
            enqueue_fiscalization_job(get_checkout_queue(), job_id, delay=2)
        except RedisError:
            logger.warning("Fiscalization retry wakeup failed; sweep will recover job=%s", job_id)
    logger.info(
        "Fiscalization job finished sale=%s job=%s attempt=%s "
        "aqsi_operation_id=%s duration_ms=%.1f status=%s",
        sale_id,
        job_id,
        attempt,
        operation_id,
        (perf_counter() - started) * 1000,
        final,
    )
    return final


def _claim_job(job_id: UUID) -> tuple[UUID, UUID, UUID, int, str | None] | None:
    with SessionLocal() as session:
        job = session.scalar(
            select(FiscalizationJob)
            .where(FiscalizationJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        now = datetime.now(UTC)
        if (
            job is None
            or job.status is not FiscalizationJobStatus.QUEUED
            or job.sale.fiscalization.operator_dismissed
            or (job.next_retry_at is not None and _as_utc(job.next_retry_at) > now)
        ):
            return None
        fiscalization = job.sale.fiscalization
        if fiscalization.status is FiscalizationStatus.UNKNOWN and (
            fiscalization.external_id is None or job.attempt_count >= MAX_UNKNOWN_QUERY_RUNS
        ):
            job.status = FiscalizationJobStatus.FAILED
            job.next_retry_at = None
            job.last_error = (
                "Fiscalization outcome requires manual review; automatic retry is blocked."
            )
            session.commit()
            return None
        job.status = FiscalizationJobStatus.PROCESSING
        job.attempt_count += 1
        job.last_attempt_at = now
        job.next_retry_at = None
        job.last_error = None
        sale_id = job.sale_id
        owner_id = job.sale.owner_id
        integration_id = job.sale.fiscalization.integration_id
        if integration_id is None:
            raise RuntimeError("Fiscalization job has no Integration.")
        session.commit()
        return (
            sale_id,
            owner_id,
            integration_id,
            job.attempt_count,
            job.sale.fiscalization.external_id,
        )


def _sync_operation(job_id: str, operation_id: str | None) -> None:
    with SessionLocal() as session:
        job = session.get(FiscalizationJob, UUID(job_id))
        if job is not None and operation_id and job.aqsi_operation_id != operation_id:
            job.aqsi_operation_id = operation_id
            session.commit()


def _finish_job(job_id: UUID, status: str, error: str | None) -> None:
    with SessionLocal() as session:
        job = session.scalar(
            select(FiscalizationJob).where(FiscalizationJob.id == job_id).with_for_update()
        )
        if job is None:
            return
        now = datetime.now(UTC)
        job.status = FiscalizationJobStatus(status)
        job.last_error = error[:1000] if error else None
        job.next_retry_at = now + timedelta(seconds=5) if status == "queued" else None
        job.completed_at = now if status == "completed" else None
        if status == "queued":
            job.sale.status = SaleStatus.FISCALIZATION_PENDING
            if job.sale.fiscalization.status is FiscalizationStatus.FAILED:
                job.sale.fiscalization.status = FiscalizationStatus.PENDING
        if status == "failed":
            job.sale.status = (
                SaleStatus.FISCALIZATION_PENDING
                if job.sale.fiscalization.status is FiscalizationStatus.UNKNOWN
                else SaleStatus.FISCALIZATION_FAILED
            )
        session.commit()


def _quarantine_job(job_id: UUID, error: str) -> None:
    """Keep an ambiguous receipt fact visible while blocking another create call."""
    with SessionLocal() as session:
        job = session.scalar(
            select(FiscalizationJob).where(FiscalizationJob.id == job_id).with_for_update()
        )
        if job is None:
            return
        job.status = FiscalizationJobStatus.FAILED
        job.last_error = error[:1000]
        job.next_retry_at = None
        job.completed_at = None
        job.sale.fiscalization.status = FiscalizationStatus.UNKNOWN
        job.sale.status = SaleStatus.FISCALIZATION_PENDING
        session.commit()


def recover_fiscalization_jobs() -> int:
    """Requeue due jobs and reclaim stale processing after application restart."""
    now = datetime.now(UTC)
    stale_before = now - FISCALIZATION_STALE_AFTER
    with SessionLocal() as session:
        jobs = session.scalars(
            select(FiscalizationJob)
            .where(
                or_(
                    (
                        (FiscalizationJob.status == FiscalizationJobStatus.QUEUED)
                        & or_(
                            FiscalizationJob.next_retry_at.is_(None),
                            FiscalizationJob.next_retry_at <= now,
                        )
                    ),
                    (
                        (FiscalizationJob.status == FiscalizationJobStatus.PROCESSING)
                        & (FiscalizationJob.updated_at < stale_before)
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        ).all()
        runnable: list[UUID] = []
        for job in jobs:
            stale = (
                job.status is FiscalizationJobStatus.PROCESSING
                and _as_utc(job.updated_at) < stale_before
            )
            fiscalization = job.sale.fiscalization
            unresolved_without_id = fiscalization.external_id is None and (
                stale or fiscalization.status is FiscalizationStatus.UNKNOWN
            )
            exhausted_unknown_query = (
                fiscalization.status is FiscalizationStatus.UNKNOWN
                and job.attempt_count >= MAX_UNKNOWN_QUERY_RUNS
            )
            if unresolved_without_id or exhausted_unknown_query:
                job.status = FiscalizationJobStatus.FAILED
                job.next_retry_at = None
                job.last_error = (
                    "Fiscalization outcome requires manual review; automatic retry is blocked."
                )
                fiscalization.status = FiscalizationStatus.UNKNOWN
                job.sale.status = SaleStatus.FISCALIZATION_PENDING
                continue
            job.status = FiscalizationJobStatus.QUEUED
            job.next_retry_at = now
            job.last_error = (
                "Recovered stale processing job."
                if job.last_attempt_at and stale
                else job.last_error
            )
            runnable.append(job.id)
        session.commit()
    queue = get_checkout_queue()
    for job_id in runnable:
        try:
            enqueue_fiscalization_job(queue, job_id)
        except RedisError:
            logger.warning("Could not wake recovered fiscalization job=%s", job_id)
    return len(runnable)


def ensure_fiscalization_recovery_schedule(redis_connection: object) -> None:
    """Register the periodic DB recovery sweep and run it once at worker startup."""
    queue = Queue(CHECKOUT_QUEUE_NAME, connection=redis_connection)
    legacy = queue.fetch_job(LEGACY_FISCALIZATION_RECOVERY_JOB_ID)
    if legacy is not None:
        legacy.delete()
    existing = queue.fetch_job(FISCALIZATION_RECOVERY_JOB_ID)
    active_statuses = {
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
    if existing is None or existing.get_status(refresh=True) not in active_statuses:
        if existing is not None:
            existing.delete()
        queue.enqueue(
            recover_fiscalization_jobs,
            job_id=FISCALIZATION_RECOVERY_JOB_ID,
            job_timeout=60,
            repeat=Repeat(
                times=FISCALIZATION_RECOVERY_REPEATS,
                interval=FISCALIZATION_RECOVERY_INTERVAL_SECONDS,
            ),
        )
    recover_fiscalization_jobs()

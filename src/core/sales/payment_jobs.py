from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter, sleep
from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Repeat
from rq.job import JobStatus
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import core.identity.models  # noqa: F401
from core.config import get_settings
from core.database import SessionLocal
from core.integrations.checkout import CheckoutProviderFactory
from core.jobs import CHECKOUT_QUEUE_NAME, get_checkout_queue
from core.sales.checkout import CheckoutService
from core.sales.enums import (
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    PaymentJobStatus,
    PaymentStatus,
    SaleStatus,
)
from core.sales.financial_events import append_financial_event
from core.sales.fiscalization_jobs import enqueue_sale_fiscalization
from core.sales.models import PaymentJob

logger = logging.getLogger(__name__)

PAYMENT_RECOVERY_INTERVAL_SECONDS = 15
PAYMENT_RECOVERY_REPEATS = 1_000_000
PAYMENT_RECOVERY_JOB_ID = "core-payment-recovery-checkout"
LEGACY_PAYMENT_RECOVERY_JOB_ID = "core-payment-recovery"
PAYMENT_STALE_AFTER = timedelta(minutes=2)
PAYMENT_POLL_DELAYS = (0.0, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
MAX_UNKNOWN_QUERY_RUNS = 3


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enqueue_payment_job(queue: Queue, job_id: UUID | str, *, delay: int = 0) -> None:
    """Wake one DB-backed acquiring job; PostgreSQL remains authoritative."""
    kwargs = {"job_timeout": 90, "result_ttl": 300, "failure_ttl": 86400}
    if delay:
        queue.enqueue_in(timedelta(seconds=delay), run_payment_job, str(job_id), **kwargs)
    else:
        queue.enqueue(run_payment_job, str(job_id), **kwargs)


def enqueue_sale_payment(sale_id: UUID | str) -> bool:
    """Best-effort wakeup for the latest queued payment job."""
    with SessionLocal() as session:
        job_id = session.scalar(
            select(PaymentJob.id)
            .where(
                PaymentJob.sale_id == UUID(str(sale_id)),
                PaymentJob.status == PaymentJobStatus.QUEUED,
            )
            .order_by(PaymentJob.created_at.desc())
            .limit(1)
        )
    if job_id is None:
        return False
    try:
        enqueue_payment_job(get_checkout_queue(), job_id)
    except RedisError:
        logger.exception("Payment wakeup failed; recovery sweep will retry sale=%s", sale_id)
        return False
    return True


def run_payment_job(job_id: str) -> str:
    """Initiate/reconcile one payment independently from browser lifetime."""
    started = perf_counter()
    claimed = _claim_job(UUID(job_id))
    if claimed is None:
        return "not_claimed"
    (
        sale_id,
        owner_id,
        integration_id,
        fiscal_integration_id,
        payment_attempt_id,
        attempt_number,
        provider_operation_id,
    ) = claimed
    final = "queued"
    provider_status: object = None
    error: str | None = None
    manual_review = False
    try:
        with SessionLocal() as session:
            binding = CheckoutProviderFactory(session, get_settings()).for_recovery(
                payment_integration_id=integration_id,
                fiscal_integration_id=fiscal_integration_id,
            )
            try:
                checkout = CheckoutService(session, binding, actor_type=FinancialActorType.WORKER)
                for delay in PAYMENT_POLL_DELAYS:
                    if delay:
                        sleep(delay)
                    sale = checkout.process_payment(
                        sale_id,
                        owner_id=owner_id,
                        attempt_id=payment_attempt_id,
                    )
                    payment = next(
                        item for item in sale.payments if item.id == payment_attempt_id
                    )
                    provider_operation_id = payment.external_id
                    provider_status = payment.provider_metadata.get("provider_status")
                    _sync_operation(UUID(job_id), provider_operation_id)
                    if (
                        payment.status is PaymentStatus.PENDING
                        and payment.accounting_superseded
                        and payment.external_id is not None
                    ):
                        sale = checkout.request_emergency_payment_cancellation(
                            sale_id,
                            owner_id=owner_id,
                            attempt_id=payment_attempt_id,
                        )
                        payment = next(
                            item for item in sale.payments if item.id == payment_attempt_id
                        )
                    if payment.status is PaymentStatus.SUCCEEDED:
                        final = "completed"
                        break
                    if payment.status in {PaymentStatus.FAILED, PaymentStatus.CANCELED}:
                        final = "completed"
                        error = payment.error_message
                        break
                    if payment.status is PaymentStatus.UNKNOWN and (
                        not payment.external_id or attempt_number >= MAX_UNKNOWN_QUERY_RUNS
                    ):
                        final = "failed"
                        error = payment.error_message or "Payment outcome is ambiguous."
                        manual_review = True
                        break
                    if (
                        sale.status is SaleStatus.COMPLETED
                        and payment.accounting_superseded
                        and payment.external_id is None
                    ):
                        final = "failed"
                        error = (
                            "Emergency fallback is effective; payment command without a "
                            "provider ID cannot be dispatched again."
                        )
                        manual_review = True
                        break
            finally:
                if binding.close is not None:
                    binding.close()
    except Exception as exc:
        logger.exception("Payment worker failed sale=%s job=%s", sale_id, job_id)
        error = str(exc)
        # If this run was responsible for initiating the command and no ID was
        # durably observed, re-running it could charge the customer twice.
        manual_review = provider_operation_id is None
        final = "failed" if manual_review else "queued"

    if manual_review:
        _quarantine_job(UUID(job_id), error or "Payment outcome requires manual review.")
    else:
        _finish_job(UUID(job_id), final, error)
    if final == "queued":
        try:
            enqueue_payment_job(get_checkout_queue(), job_id, delay=3)
        except RedisError:
            logger.warning("Payment retry wakeup failed; sweep will recover job=%s", job_id)
    elif final == "completed":
        enqueue_sale_fiscalization(sale_id)
    logger.info(
        "Payment job finished sale=%s job=%s attempt=%s provider_operation_id=%s "
        "provider_status=%s duration_ms=%.1f final_status=%s",
        sale_id,
        job_id,
        attempt_number,
        provider_operation_id,
        provider_status,
        (perf_counter() - started) * 1000,
        final,
    )
    return final


def _claim_job(
    job_id: UUID,
) -> tuple[UUID, UUID, UUID, UUID | None, UUID, int, str | None] | None:
    with SessionLocal() as session:
        job = session.scalar(
            select(PaymentJob).where(PaymentJob.id == job_id).with_for_update(skip_locked=True)
        )
        now = datetime.now(UTC)
        if (
            job is None
            or job.status is not PaymentJobStatus.QUEUED
            or (job.next_retry_at is not None and _as_utc(job.next_retry_at) > now)
        ):
            return None
        emergency_fallback = any(item.emergency_fallback for item in job.sale.payments)
        if (
            emergency_fallback
            and job.payment_attempt.accounting_superseded
            and job.payment_attempt.external_id is None
        ):
            job.status = PaymentJobStatus.FAILED
            job.next_retry_at = None
            job.last_error = (
                "Emergency fallback recorded; payment command cannot be dispatched again."
            )
            _append_manual_review_event(session, job, job.last_error)
            session.commit()
            return None
        if job.payment_attempt.status is PaymentStatus.UNKNOWN and (
            job.payment_attempt.external_id is None
            or (
                job.attempt_count >= MAX_UNKNOWN_QUERY_RUNS
                and job.reconciliation_requested_at is None
            )
        ):
            job.status = PaymentJobStatus.FAILED
            job.next_retry_at = None
            job.last_error = "Payment outcome requires manual review; automatic retry is blocked."
            _append_manual_review_event(session, job, job.last_error)
            session.commit()
            return None
        integration_id = job.payment_attempt.integration_id
        if integration_id is None:
            raise RuntimeError("Payment job has no Integration.")
        job.status = PaymentJobStatus.PROCESSING
        job.attempt_count += 1
        job.last_attempt_at = now
        job.next_retry_at = None
        job.last_error = None
        if job.payment_attempt.external_id is None and job.dispatch_started_at is None:
            job.dispatch_started_at = now
            append_financial_event(
                session,
                sale_id=job.sale_id,
                entity_type=FinancialEntityType.PAYMENT_JOB,
                entity_id=job.id,
                event_type=FinancialEventType.PAYMENT_DISPATCH_STARTED,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.WORKER,
                previous_status=PaymentJobStatus.QUEUED.value,
                new_status=PaymentJobStatus.PROCESSING.value,
                provider=job.payment_attempt.provider,
                metadata={"dispatch_started_at": now.isoformat()},
            )
        result = (
            job.sale_id,
            job.sale.owner_id,
            integration_id,
            job.payment_attempt.selected_fiscal_integration_id,
            job.payment_attempt_id,
            job.attempt_count,
            job.payment_attempt.external_id,
        )
        session.commit()
        return result


def _sync_operation(job_id: UUID, operation_id: str | None) -> None:
    if operation_id is None:
        return
    with SessionLocal() as session:
        job = session.get(PaymentJob, job_id)
        if job is not None:
            job.updated_at = datetime.now(UTC)
            session.commit()


def _finish_job(job_id: UUID, status: str, error: str | None) -> None:
    with SessionLocal() as session:
        job = session.scalar(select(PaymentJob).where(PaymentJob.id == job_id).with_for_update())
        if job is None:
            return
        now = datetime.now(UTC)
        job.status = PaymentJobStatus(status)
        job.last_error = error[:1000] if error else None
        job.next_retry_at = now + timedelta(seconds=5) if status == "queued" else None
        job.completed_at = now if status == "completed" else None
        if job.reconciliation_requested_at is not None:
            append_financial_event(
                session,
                sale_id=job.sale_id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=job.payment_attempt_id,
                event_type=(
                    FinancialEventType.RECONCILIATION_SUCCEEDED
                    if status == "completed"
                    else FinancialEventType.RECONCILIATION_INCONCLUSIVE
                ),
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.WORKER,
                previous_status=job.payment_attempt.status.value,
                new_status=job.payment_attempt.status.value,
                provider=job.payment_attempt.provider,
                provider_operation_id=job.payment_attempt.external_id,
                error_code=job.payment_attempt.error_code,
                safe_message=error
                or (
                    "Provider status was checked successfully."
                    if status == "completed"
                    else "Provider operation is still pending."
                ),
            )
            job.reconciliation_requested_at = None
        session.commit()


def _quarantine_job(job_id: UUID, error: str) -> None:
    """Keep an ambiguous acquiring fact visible while blocking another command."""
    with SessionLocal() as session:
        job = session.scalar(select(PaymentJob).where(PaymentJob.id == job_id).with_for_update())
        if job is None:
            return
        job.status = PaymentJobStatus.FAILED
        job.last_error = error[:1000]
        job.next_retry_at = None
        job.completed_at = None
        job.payment_attempt.status = PaymentStatus.UNKNOWN
        if not any(item.emergency_fallback for item in job.sale.payments):
            job.sale.status = SaleStatus.PAYMENT_PENDING
        if job.reconciliation_requested_at is not None:
            append_financial_event(
                session,
                sale_id=job.sale_id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=job.payment_attempt_id,
                event_type=FinancialEventType.RECONCILIATION_INCONCLUSIVE,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.WORKER,
                provider=job.payment_attempt.provider,
                provider_operation_id=job.payment_attempt.external_id,
                error_code=job.payment_attempt.error_code,
                safe_message=job.last_error,
            )
            job.reconciliation_requested_at = None
        _append_manual_review_event(session, job, job.last_error)
        session.commit()


def recover_payment_jobs() -> int:
    """Requeue due work and safely quarantine ambiguous stale initiations."""
    now = datetime.now(UTC)
    stale_before = now - PAYMENT_STALE_AFTER
    runnable: list[UUID] = []
    with SessionLocal() as session:
        jobs = session.scalars(
            select(PaymentJob)
            .where(
                or_(
                    (
                        (PaymentJob.status == PaymentJobStatus.QUEUED)
                        & or_(
                            PaymentJob.next_retry_at.is_(None),
                            PaymentJob.next_retry_at <= now,
                        )
                    ),
                    (
                        (PaymentJob.status == PaymentJobStatus.PROCESSING)
                        & (PaymentJob.updated_at < stale_before)
                    ),
                )
            )
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            stale = (
                job.status is PaymentJobStatus.PROCESSING and _as_utc(job.updated_at) < stale_before
            )
            unresolved_without_id = job.payment_attempt.external_id is None and (
                stale or job.payment_attempt.status is PaymentStatus.UNKNOWN
            )
            exhausted_unknown_query = (
                job.payment_attempt.status is PaymentStatus.UNKNOWN
                and job.attempt_count >= MAX_UNKNOWN_QUERY_RUNS
            )
            if unresolved_without_id or exhausted_unknown_query:
                job.status = PaymentJobStatus.FAILED
                job.next_retry_at = None
                job.last_error = (
                    "Payment outcome requires manual review; automatic retry is blocked."
                )
                job.payment_attempt.status = PaymentStatus.UNKNOWN
                if not any(item.emergency_fallback for item in job.sale.payments):
                    job.sale.status = SaleStatus.PAYMENT_PENDING
                _append_manual_review_event(session, job, job.last_error)
                continue
            job.status = PaymentJobStatus.QUEUED
            job.next_retry_at = now
            runnable.append(job.id)
        session.commit()
    queue = get_checkout_queue()
    for job_id in runnable:
        try:
            enqueue_payment_job(queue, job_id)
        except RedisError:
            logger.warning("Could not wake recovered payment job=%s", job_id)
    return len(runnable)


def ensure_payment_recovery_schedule(redis_connection: Redis) -> None:
    """Register the payment recovery sweep and run it at worker startup."""
    queue = Queue(CHECKOUT_QUEUE_NAME, connection=redis_connection)
    legacy = queue.fetch_job(LEGACY_PAYMENT_RECOVERY_JOB_ID)
    if legacy is not None:
        legacy.delete()
    existing = queue.fetch_job(PAYMENT_RECOVERY_JOB_ID)
    active = {
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
    if existing is None or existing.get_status(refresh=True) not in active:
        if existing is not None:
            existing.delete()
        queue.enqueue(
            recover_payment_jobs,
            job_id=PAYMENT_RECOVERY_JOB_ID,
            job_timeout=60,
            repeat=Repeat(
                times=PAYMENT_RECOVERY_REPEATS,
                interval=PAYMENT_RECOVERY_INTERVAL_SECONDS,
            ),
        )
    recover_payment_jobs()


def _append_manual_review_event(session: Session, job: PaymentJob, message: str) -> None:
    append_financial_event(
        session,
        sale_id=job.sale_id,
        entity_type=FinancialEntityType.PAYMENT_JOB,
        entity_id=job.id,
        event_type=FinancialEventType.MANUAL_REVIEW_REQUIRED,
        correlation_id=job.correlation_id,
        actor_type=FinancialActorType.SYSTEM,
        previous_status=PaymentJobStatus.PROCESSING.value,
        new_status=PaymentJobStatus.FAILED.value,
        provider=job.payment_attempt.provider,
        provider_operation_id=job.payment_attempt.external_id,
        error_code=job.payment_attempt.error_code,
        safe_message=message,
    )

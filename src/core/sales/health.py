from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis import Redis
from redis.exceptions import RedisError
from rq import Worker
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.config import Settings
from core.jobs import CHECKOUT_QUEUE_NAME
from core.sales.enums import FiscalizationJobStatus, PaymentJobStatus
from core.sales.models import FiscalizationJob, PaymentJob


@dataclass(frozen=True)
class CheckoutWorkerHealth:
    """Bounded worker and durable queue diagnostics for the cashier UI."""

    running: bool
    last_heartbeat: datetime | None
    queued_jobs: int
    processing_jobs: int
    oldest_queued_age_seconds: int | None
    stalled_jobs: int
    last_error: str | None


def collect_checkout_worker_health(
    session: Session, settings: Settings, *, now: datetime | None = None
) -> CheckoutWorkerHealth:
    """Combine RQ liveness with authoritative PostgreSQL job state."""
    checked_at = now or datetime.now(UTC)
    queued_jobs = _count_jobs(session, "queued")
    processing_jobs = _count_jobs(session, "processing")
    oldest_created_at = _oldest_queued_at(session)
    oldest_age = (
        max(0, int((checked_at - _as_utc(oldest_created_at)).total_seconds()))
        if oldest_created_at is not None
        else None
    )
    queued_before = checked_at - timedelta(seconds=settings.checkout_queued_job_stale_seconds)
    processing_before = checked_at - timedelta(
        seconds=settings.checkout_processing_job_stale_seconds
    )
    stalled_jobs = _count_stalled(session, queued_before, processing_before)
    last_error = _latest_job_error(session)
    latest_heartbeat: datetime | None = None
    try:
        connection = Redis.from_url(settings.redis_url)
        for worker in Worker.all(connection=connection):
            queue_names = {queue.name for queue in worker.queues}
            if CHECKOUT_QUEUE_NAME not in queue_names:
                continue
            heartbeat = getattr(worker, "last_heartbeat", None)
            if heartbeat is not None and (
                latest_heartbeat is None or _as_utc(heartbeat) > _as_utc(latest_heartbeat)
            ):
                latest_heartbeat = heartbeat
    except (RedisError, OSError):
        latest_heartbeat = None
    running = bool(
        latest_heartbeat
        and checked_at - _as_utc(latest_heartbeat)
        <= timedelta(seconds=settings.checkout_worker_stale_seconds)
    )
    return CheckoutWorkerHealth(
        running=running,
        last_heartbeat=_as_utc(latest_heartbeat) if latest_heartbeat else None,
        queued_jobs=queued_jobs,
        processing_jobs=processing_jobs,
        oldest_queued_age_seconds=oldest_age,
        stalled_jobs=stalled_jobs,
        last_error=last_error,
    )


def _count_jobs(session: Session, status: str) -> int:
    payment = session.scalar(
        select(func.count(PaymentJob.id)).where(PaymentJob.status == PaymentJobStatus(status))
    )
    fiscal = session.scalar(
        select(func.count(FiscalizationJob.id)).where(
            FiscalizationJob.status == FiscalizationJobStatus(status)
        )
    )
    return int(payment or 0) + int(fiscal or 0)


def _oldest_queued_at(session: Session) -> datetime | None:
    payment = session.scalar(
        select(func.min(PaymentJob.created_at)).where(PaymentJob.status == PaymentJobStatus.QUEUED)
    )
    fiscal = session.scalar(
        select(func.min(FiscalizationJob.created_at)).where(
            FiscalizationJob.status == FiscalizationJobStatus.QUEUED
        )
    )
    values = [value for value in (payment, fiscal) if value is not None]
    return min(values) if values else None


def _count_stalled(session: Session, queued_before: datetime, processing_before: datetime) -> int:
    payment = session.scalar(
        select(func.count(PaymentJob.id)).where(
            or_(
                (PaymentJob.status == PaymentJobStatus.QUEUED)
                & (PaymentJob.created_at < queued_before),
                (PaymentJob.status == PaymentJobStatus.PROCESSING)
                & (PaymentJob.updated_at < processing_before),
            )
        )
    )
    fiscal = session.scalar(
        select(func.count(FiscalizationJob.id)).where(
            or_(
                (FiscalizationJob.status == FiscalizationJobStatus.QUEUED)
                & (FiscalizationJob.created_at < queued_before),
                (FiscalizationJob.status == FiscalizationJobStatus.PROCESSING)
                & (FiscalizationJob.updated_at < processing_before),
            )
        )
    )
    return int(payment or 0) + int(fiscal or 0)


def _latest_job_error(session: Session) -> str | None:
    payment = session.execute(
        select(PaymentJob.last_error, PaymentJob.updated_at)
        .where(PaymentJob.last_error.is_not(None))
        .order_by(PaymentJob.updated_at.desc())
        .limit(1)
    ).first()
    fiscal = session.execute(
        select(FiscalizationJob.last_error, FiscalizationJob.updated_at)
        .where(FiscalizationJob.last_error.is_not(None))
        .order_by(FiscalizationJob.updated_at.desc())
        .limit(1)
    ).first()
    rows = [row for row in (payment, fiscal) if row is not None]
    if not rows:
        return None
    latest = max(rows, key=lambda row: _as_utc(row[1]))
    return str(latest[0])[:1000]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

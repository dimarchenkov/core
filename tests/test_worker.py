from __future__ import annotations

from unittest.mock import Mock

import pytest
from rq.job import JobStatus

from core import worker as worker_module
from core.integrations.aqsi import jobs as aqsi_jobs


def test_worker_starts_with_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delayed retries must return from RQ's scheduled registry automatically."""
    redis_connection = Mock()
    worker = Mock()
    database_session = Mock()
    session_context = Mock()
    session_context.__enter__ = Mock(return_value=database_session)
    session_context.__exit__ = Mock(return_value=None)
    validate_credentials = Mock()
    ensure_sync_schedule = Mock()

    monkeypatch.setattr(worker_module.Redis, "from_url", Mock(return_value=redis_connection))
    monkeypatch.setattr(worker_module, "build_worker", Mock(return_value=worker))
    monkeypatch.setattr(worker_module, "SessionLocal", Mock(return_value=session_context))
    monkeypatch.setattr(
        worker_module,
        "load_master_encryption_key",
        Mock(return_value="resolved-master-key"),
    )
    monkeypatch.setattr(
        worker_module,
        "validate_stored_integration_credentials",
        validate_credentials,
    )
    monkeypatch.setattr(
        worker_module,
        "ensure_aqsi_catalog_sync_schedule",
        ensure_sync_schedule,
    )

    worker_module.main()

    validate_credentials.assert_called_once_with(database_session, "resolved-master-key")
    ensure_sync_schedule.assert_called_once_with(redis_connection)
    worker.work.assert_called_once_with(with_scheduler=True)


def test_aqsi_periodic_sync_schedule_is_registered_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker bootstrap creates one five-minute repeat job and reuses an active schedule."""

    class FakeQueue:
        def __init__(self) -> None:
            self.existing: Mock | None = None
            self.calls: list[tuple[object, dict[str, object]]] = []

        def fetch_job(self, job_id: str) -> Mock | None:
            assert job_id == aqsi_jobs.AQSI_CATALOG_SYNC_SCHEDULE_JOB_ID
            return self.existing

        def enqueue(self, function: object, **kwargs: object) -> None:
            self.calls.append((function, kwargs))

    queue = FakeQueue()
    monkeypatch.setattr(aqsi_jobs, "Queue", lambda *args, **kwargs: queue)

    assert aqsi_jobs.ensure_aqsi_catalog_sync_schedule(Mock()) is True
    function, options = queue.calls[0]
    repeat = options["repeat"]
    assert function is aqsi_jobs.synchronize_aqsi_catalog
    assert options["job_id"] == aqsi_jobs.AQSI_CATALOG_SYNC_SCHEDULE_JOB_ID
    assert repeat.intervals == [aqsi_jobs.AQSI_CATALOG_SYNC_INTERVAL_SECONDS]

    queue.existing = Mock()
    queue.existing.get_status.return_value = JobStatus.SCHEDULED
    assert aqsi_jobs.ensure_aqsi_catalog_sync_schedule(Mock()) is False
    assert len(queue.calls) == 1

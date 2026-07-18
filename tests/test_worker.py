from __future__ import annotations

from unittest.mock import Mock

import pytest

from core import worker as worker_module


def test_worker_starts_with_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delayed retries must return from RQ's scheduled registry automatically."""
    redis_connection = Mock()
    worker = Mock()

    monkeypatch.setattr(worker_module.Redis, "from_url", Mock(return_value=redis_connection))
    monkeypatch.setattr(worker_module, "build_worker", Mock(return_value=worker))

    worker_module.main()

    worker.work.assert_called_once_with(with_scheduler=True)

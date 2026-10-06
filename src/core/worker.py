from __future__ import annotations

from redis import Redis
from rq import Worker

from core.config import get_settings
from core.database import SessionLocal
from core.integrations.aqsi.jobs import ensure_aqsi_catalog_sync_schedule
from core.integrations.credentials import validate_stored_integration_credentials
from core.integrations.master_key import load_master_encryption_key
from core.jobs import DEFAULT_QUEUE_NAME
from core.logging import configure_logging


def build_worker(redis_connection: Redis) -> Worker:
    """Build an RQ worker that processes Core background jobs."""
    return Worker([DEFAULT_QUEUE_NAME], connection=redis_connection)


def main() -> None:
    """Start the RQ worker process for local Docker Compose runs."""
    settings = get_settings()
    configure_logging(settings.log_level)
    key = load_master_encryption_key(settings)
    with SessionLocal() as session:
        validate_stored_integration_credentials(session, key)
    redis_connection = Redis.from_url(settings.redis_url)
    ensure_aqsi_catalog_sync_schedule(redis_connection)
    build_worker(redis_connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()

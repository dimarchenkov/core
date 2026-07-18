from __future__ import annotations

from redis import Redis
from rq import Worker

from core.config import get_settings
from core.jobs import DEFAULT_QUEUE_NAME
from core.logging import configure_logging


def build_worker(redis_connection: Redis) -> Worker:
    """Build an RQ worker that processes Core background jobs."""
    return Worker([DEFAULT_QUEUE_NAME], connection=redis_connection)


def main() -> None:
    """Start the RQ worker process for local Docker Compose runs."""
    settings = get_settings()
    configure_logging(settings.log_level)
    redis_connection = Redis.from_url(settings.redis_url)
    build_worker(redis_connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()

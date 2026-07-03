from __future__ import annotations

from redis import Redis
from rq import Queue

from core.config import get_settings

DEFAULT_QUEUE_NAME = "default"


def get_redis_connection() -> Redis:
    """Create a Redis connection used by queues and background workers."""
    return Redis.from_url(get_settings().redis_url)


def get_default_queue() -> Queue:
    """Return the default RQ queue for infrastructure-level background jobs."""
    return Queue(DEFAULT_QUEUE_NAME, connection=get_redis_connection())

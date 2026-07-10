from __future__ import annotations

from secrets import randbits
from time import time_ns
from uuid import UUID

type UUIDv7 = UUID


def generate_uuid_v7() -> UUIDv7:
    """Generate a UUIDv7 value for time-sortable database primary keys."""
    timestamp_ms = time_ns() // 1_000_000
    random_a = randbits(12)
    random_b = randbits(62)

    uuid_int = (
        ((timestamp_ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | (random_a << 64)
        | (0b10 << 62)
        | random_b
    )
    return UUID(int=uuid_int)

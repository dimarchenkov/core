from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from secrets import randbits
from time import time_ns
from uuid import UUID

from sqlalchemy import DateTime, Uuid, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from core.config import get_settings


def generate_uuid_v7() -> UUID:
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


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models owned by Core modules."""


class UUIDPrimaryKeyMixin:
    """Provide the shared UUIDv7 primary key column for database models."""

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=generate_uuid_v7,
    )


class TimestampMixin:
    """Track creation and last update timestamps for database records."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    """Track soft deletion without removing records from the database."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        """Return whether the record has been soft-deleted."""
        return self.deleted_at is not None

    def soft_delete(self, deleted_by_id: UUID | None = None) -> None:
        """Mark the record as deleted while preserving it for history and restore."""
        self.deleted_at = datetime.now(UTC)
        self.deleted_by_id = deleted_by_id

    def restore(self) -> None:
        """Clear soft deletion markers so the record is active again."""
        self.deleted_at = None
        self.deleted_by_id = None


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session]:
    """Provide a database session for request handlers and services."""
    with SessionLocal() as session:
        yield session

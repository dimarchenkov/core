from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.shared.db.types import UUIDv7, generate_uuid_v7


class UUIDPrimaryKeyMixin:
    """Provide the shared UUIDv7 primary key column for database models."""

    id: Mapped[UUIDv7] = mapped_column(
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
    deleted_by_id: Mapped[UUIDv7 | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

    @property
    def is_deleted(self) -> bool:
        """Return whether the record has been soft-deleted."""
        return self.deleted_at is not None

    def soft_delete(self, deleted_by_id: UUIDv7 | None = None) -> None:
        """Mark the record as deleted while preserving it for history and restore."""
        self.deleted_at = datetime.now(UTC)
        self.deleted_by_id = deleted_by_id

    def restore(self) -> None:
        """Clear soft deletion markers so the record is active again."""
        self.deleted_at = None
        self.deleted_by_id = None


class VersionMixin:
    """Store an integer record version for future concurrency checks."""

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class UserTrackingMixin:
    """Store optional user identifiers for record creation and last update."""

    created_by_id: Mapped[UUIDv7 | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_by_id: Mapped[UUIDv7 | None] = mapped_column(Uuid(as_uuid=True), nullable=True)

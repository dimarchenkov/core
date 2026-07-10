from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from core.shared.db.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    UserTrackingMixin,
    UUIDPrimaryKeyMixin,
    VersionMixin,
)


class Base(DeclarativeBase):
    """Declarative base for SQLAlchemy models owned by Core modules."""


class BaseModel(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    VersionMixin,
    UserTrackingMixin,
    Base,
):
    """Abstract base model with shared infrastructure columns for entities."""

    __abstract__ = True

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.shared.db import Base, BaseModel, UUIDPrimaryKeyMixin, UUIDv7


class PrivilegeAuditAction(StrEnum):
    """Superuser privilege changes recorded by the local identity CLI."""

    SUPERUSER_ENABLED = "superuser_enabled"
    SUPERUSER_DISABLED = "superuser_disabled"


def _privilege_audit_action_values(enum_class: type[PrivilegeAuditAction]) -> list[str]:
    """Return the database values for privilege audit actions."""
    return [action.value for action in enum_class]


class User(BaseModel):
    """Application account used for future authentication and audit attribution."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    privilege_audit_events: Mapped[list[PrivilegeAuditEvent]] = relationship(
        "PrivilegeAuditEvent",
        back_populates="target_user",
    )


class PrivilegeAuditEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only record of a local CLI superuser privilege change."""

    __tablename__ = "privilege_audit_events"

    target_user_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action: Mapped[PrivilegeAuditAction] = mapped_column(
        Enum(
            PrivilegeAuditAction,
            name="privilege_audit_action",
            values_callable=_privilege_audit_action_values,
        ),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_description: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    target_user: Mapped[User] = relationship("User", back_populates="privilege_audit_events")

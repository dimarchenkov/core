from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.identity.models import PrivilegeAuditEvent, User
from core.shared.db import UUIDv7


class UserRepository:
    """Database access for application users."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, user: User) -> User:
        """Add a user to the current unit of work."""
        self._session.add(user)
        return user

    def get_by_email(self, email: str) -> User | None:
        """Return a user by normalized email, including soft-deleted users."""
        return self._session.scalar(select(User).where(User.email == email))

    def get_active_by_email(self, email: str) -> User | None:
        """Return an active non-deleted user by normalized email."""
        return self._session.scalar(
            select(User).where(
                User.email == email,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
            )
        )

    def get_active_by_id(self, user_id: UUIDv7) -> User | None:
        """Return an active non-deleted user by identifier."""
        return self._session.scalar(
            select(User).where(
                User.id == user_id,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
            )
        )


class PrivilegeAuditEventRepository:
    """Database access for append-only privilege audit events."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to a database session."""
        self._session = session

    def add(self, event: PrivilegeAuditEvent) -> PrivilegeAuditEvent:
        """Append a privilege audit event to the current unit of work."""
        self._session.add(event)
        return event

    def list_for_user(self, user_id: UUIDv7) -> Sequence[PrivilegeAuditEvent]:
        """Return audit events for a user in chronological order."""
        return self._session.scalars(
            select(PrivilegeAuditEvent)
            .where(PrivilegeAuditEvent.target_user_id == user_id)
            .order_by(PrivilegeAuditEvent.occurred_at)
        ).all()

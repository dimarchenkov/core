from __future__ import annotations

from sqlalchemy.orm import Session

from core.identity.models import PrivilegeAuditAction, PrivilegeAuditEvent, User
from core.identity.repository import PrivilegeAuditEventRepository, UserRepository
from core.identity.security import hash_password, verify_password


class UserAlreadyExistsError(Exception):
    """Raised when a normalized email is already assigned to a user."""


class UserNotFoundOrInactiveError(Exception):
    """Raised when a target user is missing, deleted, or inactive."""


class PasswordValidationError(Exception):
    """Raised when an administrator password does not meet the minimum policy."""


class FullNameRequiredError(Exception):
    """Raised when administrator creation has no meaningful full name."""


class InvalidCredentialsError(Exception):
    """Raised when login credentials cannot authenticate an active user."""


class SuperuserReasonRequiredError(Exception):
    """Raised when enabling superuser access has no meaningful reason."""


class SuperuserAlreadyEnabledError(Exception):
    """Raised when an enabled superuser is enabled again."""


class SuperuserAlreadyDisabledError(Exception):
    """Raised when a disabled superuser is disabled again."""


def normalize_email(email: str) -> str:
    """Normalize email addresses for unique login identity comparisons."""
    return email.strip().lower()


class IdentityService:
    """Local identity operations used by administrative command-line workflows."""

    def __init__(self, session: Session) -> None:
        """Create an identity service using the given database session."""
        self._session = session
        self._users = UserRepository(session)
        self._audit_events = PrivilegeAuditEventRepository(session)

    def create_admin(self, email: str, full_name: str, password: str) -> User:
        """Create an active administrator account without superuser privileges."""
        normalized_email = normalize_email(email)
        normalized_full_name = full_name.strip()
        self._validate_admin_password(password)
        if not normalized_full_name:
            raise FullNameRequiredError
        if self._users.get_by_email(normalized_email) is not None:
            raise UserAlreadyExistsError
        user = User(
            email=normalized_email,
            full_name=normalized_full_name,
            password_hash=hash_password(password),
            is_active=True,
            is_admin=True,
            is_superuser=False,
        )
        self._users.add(user)
        self._session.commit()
        self._session.refresh(user)
        return user

    def authenticate_user(self, email: str, password: str) -> User:
        """Authenticate an active user without revealing credential failure details."""
        user = self._users.get_active_by_email(normalize_email(email))
        if user is None:
            raise InvalidCredentialsError
        try:
            password_valid = verify_password(password, user.password_hash)
        except ValueError:
            password_valid = False
        if not password_valid:
            raise InvalidCredentialsError
        return user

    def enable_superuser(self, email: str, reason: str, actor_description: str) -> User:
        """Enable emergency superuser access and append an audit event atomically."""
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise SuperuserReasonRequiredError
        return self._change_superuser(
            email=normalize_email(email),
            enabled=True,
            reason=normalized_reason,
            actor_description=actor_description,
        )

    def disable_superuser(
        self,
        email: str,
        reason: str | None,
        actor_description: str,
    ) -> User:
        """Disable emergency superuser access and append an audit event atomically."""
        normalized_reason = reason.strip() if reason is not None else None
        return self._change_superuser(
            email=normalize_email(email),
            enabled=False,
            reason=normalized_reason or None,
            actor_description=actor_description,
        )

    def _change_superuser(
        self,
        email: str,
        enabled: bool,
        reason: str | None,
        actor_description: str,
    ) -> User:
        """Apply a superuser change and its corresponding audit event in one commit."""
        user = self._users.get_active_by_email(email)
        if user is None:
            raise UserNotFoundOrInactiveError
        if enabled and user.is_superuser:
            raise SuperuserAlreadyEnabledError
        if not enabled and not user.is_superuser:
            raise SuperuserAlreadyDisabledError

        action = (
            PrivilegeAuditAction.SUPERUSER_ENABLED
            if enabled
            else PrivilegeAuditAction.SUPERUSER_DISABLED
        )
        user.is_superuser = enabled
        event = PrivilegeAuditEvent(
            target_user_id=user.id,
            action=action,
            reason=reason,
            actor_description=actor_description,
        )
        try:
            self._audit_events.add(event)
            self._session.commit()
            self._session.refresh(user)
        except Exception:
            self._session.rollback()
            raise
        return user

    def _validate_admin_password(self, password: str) -> None:
        """Reject empty, whitespace-only, and too-short administrator passwords."""
        if not password.strip() or len(password) < 8:
            raise PasswordValidationError

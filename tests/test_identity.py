from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.identity import cli
from core.identity.admin import PrivilegeAuditEventAdmin, UserAdmin
from core.identity.models import PrivilegeAuditAction, PrivilegeAuditEvent, User
from core.identity.repository import PrivilegeAuditEventRepository
from core.identity.schemas import UserRead
from core.identity.security import hash_password, verify_password
from core.identity.service import (
    FullNameRequiredError,
    IdentityService,
    PasswordValidationError,
    SuperuserAlreadyDisabledError,
    SuperuserReasonRequiredError,
    UserAlreadyExistsError,
    UserNotFoundOrInactiveError,
)
from core.shared.db import Base


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for identity foundation tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, PrivilegeAuditEvent.__table__])
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


def create_admin(service: IdentityService, email: str = "admin@example.com") -> User:
    """Create a valid ordinary administrator for identity service tests."""
    return service.create_admin(email, "Core Admin", "long enough password")


def test_create_admin_normalizes_email(session: Session) -> None:
    """Administrator creation stores trimmed lower-case login identifiers."""
    user = create_admin(IdentityService(session), "  ADMIN@Example.COM  ")

    assert user.email == "admin@example.com"


def test_create_admin_rejects_duplicate_normalized_email(session: Session) -> None:
    """Duplicate emails are compared after normalization."""
    service = IdentityService(session)
    create_admin(service, "admin@example.com")

    with pytest.raises(UserAlreadyExistsError):
        create_admin(service, " ADMIN@EXAMPLE.COM ")


def test_create_admin_rejects_whitespace_only_full_name_without_persisting_user(
    session: Session,
) -> None:
    """Administrator creation rejects missing names before adding a user to the session."""
    with pytest.raises(FullNameRequiredError):
        IdentityService(session).create_admin(
            "admin@example.com",
            "   ",
            "long enough password",
        )

    assert session.query(User).count() == 0


def test_create_admin_trims_full_name_before_storage(session: Session) -> None:
    """Administrator creation stores a normalized display name."""
    user = IdentityService(session).create_admin(
        "admin@example.com",
        "  Core Admin  ",
        "long enough password",
    )

    assert user.full_name == "Core Admin"


def test_create_admin_accepts_eight_character_password(session: Session) -> None:
    """Administrator creation accepts the eight-character minimum password length."""
    user = IdentityService(session).create_admin(
        "admin@example.com",
        "Core Admin",
        "password",
    )

    assert user.email == "admin@example.com"


def test_create_admin_rejects_passwords_shorter_than_eight_characters(session: Session) -> None:
    """Administrator creation rejects passwords shorter than the new minimum."""
    with pytest.raises(PasswordValidationError):
        IdentityService(session).create_admin(
            "admin@example.com",
            "Core Admin",
            "short",
        )


def test_password_hash_uses_argon2id_and_verifies() -> None:
    """Password security uses Argon2id hashes and rejects wrong passwords."""
    password_hash = hash_password("long enough password")

    assert password_hash.startswith("$argon2id$")
    assert verify_password("long enough password", password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_user_schema_never_exposes_password_hash() -> None:
    """Safe user schemas do not include password hashes or plain-text passwords."""
    assert "password_hash" not in UserRead.model_fields
    assert "password" not in UserRead.model_fields


def test_create_admin_creates_admin_without_superuser(session: Session) -> None:
    """Initial administrators are active admins but never emergency superusers."""
    user = create_admin(IdentityService(session))

    assert user.is_active is True
    assert user.is_admin is True
    assert user.is_superuser is False
    assert user.created_by_id is None


def test_enable_superuser_requires_reason(session: Session) -> None:
    """Emergency privilege activation requires a meaningful audit reason."""
    user = create_admin(IdentityService(session))

    with pytest.raises(SuperuserReasonRequiredError):
        IdentityService(session).enable_superuser(user.email, "   ", "local:test")

    assert session.query(PrivilegeAuditEvent).count() == 0


def test_enable_superuser_creates_one_audit_event(session: Session) -> None:
    """Successful superuser activation atomically changes the user and records an event."""
    user = create_admin(IdentityService(session))
    updated = IdentityService(session).enable_superuser(
        user.email,
        "Emergency repair",
        "local:test",
    )
    events = session.query(PrivilegeAuditEvent).all()

    assert updated.is_superuser is True
    assert len(events) == 1
    assert events[0].target_user_id == user.id
    assert events[0].action is PrivilegeAuditAction.SUPERUSER_ENABLED
    assert events[0].reason == "Emergency repair"


def test_disable_superuser_creates_one_audit_event(session: Session) -> None:
    """Successful superuser deactivation creates a separate append-only event."""
    service = IdentityService(session)
    user = create_admin(service)
    service.enable_superuser(user.email, "Emergency repair", "local:test")
    updated = service.disable_superuser(user.email, None, "local:test")
    events = session.query(PrivilegeAuditEvent).all()

    assert updated.is_superuser is False
    assert [event.action for event in events] == [
        PrivilegeAuditAction.SUPERUSER_ENABLED,
        PrivilegeAuditAction.SUPERUSER_DISABLED,
    ]


def test_failed_privilege_operations_create_no_audit_event(session: Session) -> None:
    """Missing and already-disabled user operations leave the audit table unchanged."""
    service = IdentityService(session)
    user = create_admin(service)

    with pytest.raises(SuperuserAlreadyDisabledError):
        service.disable_superuser(user.email, None, "local:test")
    with pytest.raises(UserNotFoundOrInactiveError):
        service.enable_superuser("missing@example.com", "Repair", "local:test")

    assert session.query(PrivilegeAuditEvent).count() == 0


def test_superuser_change_and_audit_event_roll_back_together(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An audit persistence failure leaves the target user's privilege unchanged."""
    service = IdentityService(session)
    user = create_admin(service)

    def fail_to_append(
        self: PrivilegeAuditEventRepository,
        event: PrivilegeAuditEvent,
    ) -> PrivilegeAuditEvent:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(PrivilegeAuditEventRepository, "add", fail_to_append)

    with pytest.raises(RuntimeError, match="audit write failed"):
        service.enable_superuser(user.email, "Repair", "local:test")

    session.refresh(user)
    assert user.is_superuser is False
    assert session.query(PrivilegeAuditEvent).count() == 0


def test_sqladmin_excludes_superuser_editing_and_audit_is_read_only() -> None:
    """SQLAdmin never accepts superuser edits and exposes audit records read-only."""
    assert User.password_hash in UserAdmin.form_excluded_columns
    assert User.is_superuser in UserAdmin.form_excluded_columns
    assert PrivilegeAuditEventAdmin.can_create is False
    assert PrivilegeAuditEventAdmin.can_edit is False
    assert PrivilegeAuditEventAdmin.can_delete is False


def test_cli_reports_missing_identity_tables_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI explains how to upgrade a database missing the identity schema."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    missing_schema_session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(cli, "SessionLocal", missing_schema_session_factory)

    exit_code = cli.main(["enable-superuser", "admin@example.com", "--reason", "Repair"])

    assert exit_code == 1
    assert capsys.readouterr().err == (
        "Database schema is not up to date.\n\n"
        "Run:\n\n"
        "docker compose exec api uv run alembic upgrade head\n"
    )

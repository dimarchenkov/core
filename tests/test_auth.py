from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import get_session
from core.identity.models import PrivilegeAuditEvent, User
from core.identity.security import create_access_token
from core.identity.service import IdentityService
from core.main import create_app
from core.shared.db import Base, generate_uuid_v7


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database for authentication endpoint tests."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, PrivilegeAuditEvent.__table__])
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a test client using the in-memory authentication database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(session: Session) -> User:
    """Create a regular active user with a known password."""
    return IdentityService(session).create_admin(
        "admin@example.com",
        "Core Admin",
        "long enough password",
    )


def login(client: TestClient, email: str, password: str) -> object:
    """Submit the OAuth2 login form using email in the username field."""
    return client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )


def authorization_header(token: str) -> dict[str, str]:
    """Build a bearer authorization header for authenticated API calls."""
    return {"Authorization": f"Bearer {token}"}


def test_valid_login_returns_bearer_token(client: TestClient, user: User) -> None:
    """Valid credentials return a usable bearer access token."""
    response = login(client, user.email, "long enough password")

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"]
    assert "password_hash" not in response.json()


def test_login_normalizes_email(client: TestClient, user: User) -> None:
    """Login accepts email addresses with casing and whitespace differences."""
    response = login(client, "  ADMIN@EXAMPLE.COM  ", "long enough password")

    assert response.status_code == 200


def test_login_errors_do_not_reveal_email_existence(client: TestClient, user: User) -> None:
    """Wrong email and password share the same generic 401 response."""
    wrong_email = login(client, "missing@example.com", "long enough password")
    wrong_password = login(client, user.email, "incorrect password")

    assert wrong_email.status_code == 401
    assert wrong_password.status_code == 401
    assert wrong_email.json()["detail"] == wrong_password.json()["detail"]


def test_inactive_user_cannot_log_in(client: TestClient, session: Session, user: User) -> None:
    """Inactive users cannot receive access tokens."""
    user.is_active = False
    session.commit()

    response = login(client, user.email, "long enough password")

    assert response.status_code == 401


def test_valid_token_accesses_current_user(client: TestClient, user: User) -> None:
    """A valid bearer token returns the safe current-user representation."""
    token = login(client, user.email, "long enough password").json()["access_token"]

    response = client.get("/api/auth/me", headers=authorization_header(token))

    assert response.status_code == 200
    assert response.json()["email"] == user.email
    assert "password_hash" not in response.json()


def test_expired_token_is_rejected(client: TestClient, user: User) -> None:
    """Expired access tokens cannot access protected identity endpoints."""
    token = create_access_token(
        user.id,
        "test-only-jwt-secret-at-least-32-bytes",
        "HS256",
        -1,
    )

    response = client.get("/api/auth/me", headers=authorization_header(token))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_malformed_token_is_rejected(client: TestClient) -> None:
    """Malformed bearer tokens are rejected without leaking decoder details."""
    response = client.get("/api/auth/me", headers=authorization_header("not-a-jwt"))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_token_for_missing_user_is_rejected(client: TestClient) -> None:
    """Tokens for deleted or missing user records cannot authenticate requests."""
    token = create_access_token(
        generate_uuid_v7(),
        "test-only-jwt-secret-at-least-32-bytes",
        "HS256",
        60,
    )

    response = client.get("/api/auth/me", headers=authorization_header(token))

    assert response.status_code == 401


def test_token_for_inactive_user_is_rejected(
    client: TestClient,
    session: Session,
    user: User,
) -> None:
    """Token validation rechecks user activity after token issuance."""
    token = login(client, user.email, "long enough password").json()["access_token"]
    user.is_active = False
    session.commit()

    response = client.get("/api/auth/me", headers=authorization_header(token))

    assert response.status_code == 401

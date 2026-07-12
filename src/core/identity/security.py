from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pwdlib import PasswordHash

from core.shared.db import UUIDv7

_password_hash = PasswordHash.recommended()


class AuthenticationTokenError(Exception):
    """Raised when a JWT cannot safely identify an authenticated user."""


def hash_password(password: str) -> str:
    """Hash a plain-text password with pwdlib's recommended Argon2id settings."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plain-text password against an Argon2id password hash."""
    return _password_hash.verify(password, password_hash)


def create_access_token(
    user_id: UUIDv7,
    secret: str,
    algorithm: str,
    expires_minutes: int,
) -> str:
    """Create a signed access token with subject, issued-at, and expiration claims."""
    issued_at = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, secret: str, algorithm: str) -> UUIDv7:
    """Decode a valid access token or raise a dedicated authentication error."""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        subject = payload["sub"]
        return UUID(subject)
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise AuthenticationTokenError("Invalid access token.") from exc

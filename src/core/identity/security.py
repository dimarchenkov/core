from __future__ import annotations

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a plain-text password with pwdlib's recommended Argon2id settings."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plain-text password against an Argon2id password hash."""
    return _password_hash.verify(password, password_hash)

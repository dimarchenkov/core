from __future__ import annotations

import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from core.config import Settings
from core.integrations.master_key import (
    MasterEncryptionKeyMismatchError,
    load_master_encryption_key,
    validate_master_encryption_key,
)

FERNET_ALGORITHM = "fernet-aes128-cbc-hmac-sha256"


class CredentialDecryptionError(Exception):
    """Raised when stored ciphertext cannot be authenticated and decrypted."""


class CredentialCipher:
    """Encrypt provider credentials with the standard authenticated Fernet format."""

    def __init__(self, encoded_key: str) -> None:
        """Validate and retain one URL-safe 32-byte Fernet master key."""
        validated_key = validate_master_encryption_key(encoded_key, source="application settings")
        self._fernet = Fernet(validated_key.encode("ascii"))
        self.key_version = hashlib.sha256(validated_key.encode("ascii")).hexdigest()[:12]

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        """Build a cipher through the canonical env/file/bootstrap key loader."""
        return cls(load_master_encryption_key(settings))

    def encrypt(self, plaintext: str) -> str:
        """Return authenticated ciphertext for one non-empty provider secret."""
        if not plaintext:
            raise ValueError("Credential value must not be empty.")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Authenticate and decrypt one stored provider credential transiently."""
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise CredentialDecryptionError("Stored credential cannot be decrypted.") from exc


def validate_stored_integration_credentials(session: Session, encoded_key: str) -> None:
    """Fail safely when the resolved key cannot decrypt persisted credentials."""
    bind = session.get_bind()
    if not inspect(bind).has_table("integration_credentials"):
        return

    from core.integrations.models import IntegrationCredential

    cipher = CredentialCipher(encoded_key)
    credentials = session.scalars(select(IntegrationCredential)).all()
    for credential in credentials:
        try:
            cipher.decrypt(credential.secret_ciphertext)
        except CredentialDecryptionError as exc:
            raise MasterEncryptionKeyMismatchError(
                "Resolved master encryption key cannot decrypt stored integration credentials. "
                "Refusing to continue."
            ) from exc

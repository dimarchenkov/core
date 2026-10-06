from __future__ import annotations

import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cryptography.fernet import Fernet
from pydantic import SecretStr

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class MasterKeySettings(Protocol):
    """Settings required by the canonical master-key loader."""

    master_encryption_key: SecretStr | None
    master_encryption_key_file: Path


class MasterEncryptionKeyError(RuntimeError):
    """Raised when Core cannot safely resolve its credential master key."""


class MasterEncryptionKeyMismatchError(MasterEncryptionKeyError):
    """Raised when two configured key sources disagree."""


def validate_master_encryption_key(value: str, *, source: str) -> str:
    """Validate one URL-safe base64 Fernet key without exposing its value."""
    try:
        encoded = value.strip().encode("ascii")
        Fernet(encoded)
    except (UnicodeEncodeError, ValueError) as exc:
        raise MasterEncryptionKeyError(
            f"Master encryption key from {source} is malformed; expected a Fernet key."
        ) from exc
    if not value.strip():
        raise MasterEncryptionKeyError(
            f"Master encryption key from {source} is malformed; expected a Fernet key."
        )
    return value.strip()


def load_master_encryption_key(settings: MasterKeySettings) -> str:
    """Resolve, validate, or atomically bootstrap Core's persistent master key."""
    key_path = settings.master_encryption_key_file
    environment_secret = settings.master_encryption_key
    if environment_secret is not None:
        environment_key = validate_master_encryption_key(
            environment_secret.get_secret_value(),
            source="MASTER_ENCRYPTION_KEY environment variable",
        )
        if key_path.exists():
            with _key_file_lock(key_path):
                persistent_key = _read_persistent_key(key_path)
            if environment_key != persistent_key:
                raise MasterEncryptionKeyMismatchError(
                    "MASTER_ENCRYPTION_KEY does not match the persistent master-key file. "
                    "Refusing to choose a key automatically."
                )
        logger.info("Master encryption key loaded from environment.")
        return environment_key

    with _key_file_lock(key_path):
        if key_path.exists():
            key = _read_persistent_key(key_path)
            logger.info("Master encryption key loaded from persistent secret file.")
            return key
        key = Fernet.generate_key().decode("ascii")
        _write_persistent_key(key_path, key)
        logger.info("Master encryption key generated and persisted.")
        return key


@contextmanager
def _key_file_lock(key_path: Path) -> Iterator[None]:
    """Serialize first-run bootstrap across API and worker processes with flock."""
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - Core deployment is Unix-based.
        raise MasterEncryptionKeyError(
            "Persistent master-key bootstrap requires filesystem locking support."
        ) from exc

    directory = key_path.parent
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        lock_path = directory / f".{key_path.name}.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
    except OSError as exc:
        raise MasterEncryptionKeyError(
            f"Cannot prepare persistent master-key directory: {directory}."
        ) from exc

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _read_persistent_key(key_path: Path) -> str:
    """Read and validate the existing key without ever replacing malformed data."""
    try:
        value = key_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise MasterEncryptionKeyError(
            f"Persistent master-key file cannot be read: {key_path}."
        ) from exc
    key = validate_master_encryption_key(value, source=f"persistent file {key_path}")
    try:
        os.chmod(key_path, 0o600)
    except OSError as exc:
        raise MasterEncryptionKeyError(
            f"Cannot enforce mode 0600 on persistent master-key file: {key_path}."
        ) from exc
    return key


def _write_persistent_key(key_path: Path, key: str) -> None:
    """Atomically publish a complete key file while holding the bootstrap lock."""
    temporary_fd = -1
    temporary_path: Path | None = None
    try:
        temporary_fd, raw_path = tempfile.mkstemp(
            prefix=f".{key_path.name}.",
            dir=key_path.parent,
        )
        temporary_path = Path(raw_path)
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, "w", encoding="ascii") as stream:
            temporary_fd = -1
            stream.write(f"{key}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, key_path)
        temporary_path = None
        os.chmod(key_path, 0o600)
        _sync_directory(key_path.parent)
    except OSError as exc:
        raise MasterEncryptionKeyError(
            f"Cannot persist master encryption key to: {key_path}."
        ) from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _sync_directory(directory: Path) -> None:
    """Durably record the atomic rename where directory fsync is supported."""
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)

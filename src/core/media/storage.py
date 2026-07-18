from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from core.shared.db import UUIDv7


class InvalidStorageKeyError(ValueError):
    """Raised when a storage key is not a safe relative path."""


class LocalImageStorage:
    """Store immutable image source files under a configured local root."""

    def __init__(self, root: Path) -> None:
        """Create storage rooted at the given local filesystem path."""
        self._root = root.resolve()

    def build_source_key(self, image_id: UUIDv7, extension: str) -> str:
        """Build a relative date-organized source key for an image identifier."""
        created_at = datetime.now(UTC)
        return f"images/source/{created_at:%Y}/{created_at:%m}/{image_id}.{extension.lower()}"

    def save_source(self, key: str, content: bytes) -> None:
        """Atomically write a new source file without altering uploaded bytes."""
        destination = self._destination_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="xb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as source_file:
                temporary_path = Path(source_file.name)
                source_file.write(content)
                source_file.flush()
                os.fsync(source_file.fileno())
            if destination.exists():
                raise FileExistsError(f"Source file already exists: {key}")
            temporary_path.replace(destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def delete_saved_source(self, key: str) -> None:
        """Remove a newly saved source only after failed metadata persistence."""
        destination = self._destination_for(key)
        if destination.is_file():
            destination.unlink()

    def path_for_key(self, key: str) -> Path:
        """Return the local path for a safe relative storage key."""
        return self._destination_for(key)

    def _destination_for(self, key: str) -> Path:
        """Resolve a relative storage key while preventing path traversal."""
        relative_path = PurePosixPath(key)
        if not key or relative_path.is_absolute() or ".." in relative_path.parts:
            raise InvalidStorageKeyError("Storage key must be a relative path.")
        destination = (self._root / Path(*relative_path.parts)).resolve()
        if not destination.is_relative_to(self._root):
            raise InvalidStorageKeyError("Storage key escapes the storage root.")
        return destination

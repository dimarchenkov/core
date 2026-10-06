from __future__ import annotations

import logging
import stat
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.identity.models import User
from core.integrations.credentials import (
    CredentialCipher,
    validate_stored_integration_credentials,
)
from core.integrations.enums import IntegrationProvider
from core.integrations.master_key import (
    MasterEncryptionKeyError,
    MasterEncryptionKeyMismatchError,
    load_master_encryption_key,
)
from core.integrations.models import Integration, IntegrationCredential
from core.integrations.schemas import IntegrationCreate
from core.integrations.service import IntegrationService
from core.shared.db import Base


@dataclass(frozen=True)
class LoaderSettings:
    """Minimal settings object accepted by the canonical key loader."""

    master_encryption_key_file: Path
    master_encryption_key: SecretStr | None = None


def settings_for(path: Path, key: str | None = None) -> LoaderSettings:
    """Build isolated loader settings for one test key path."""
    return LoaderSettings(
        master_encryption_key_file=path,
        master_encryption_key=SecretStr(key) if key is not None else None,
    )


def load_key_in_separate_process(path: str) -> str:
    """Model one independently started API or worker process."""
    return load_master_encryption_key(settings_for(Path(path)))


def test_environment_key_wins_when_valid(tmp_path: Path) -> None:
    """An explicit environment-style secret is used without creating a file."""
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / "master_encryption_key"

    resolved = load_master_encryption_key(settings_for(key_path, key))

    assert resolved == key
    assert not key_path.exists()


def test_existing_persistent_key_loads_with_restrictive_permissions(tmp_path: Path) -> None:
    """A valid existing key remains authoritative and is restricted to mode 0600."""
    key = Fernet.generate_key().decode("ascii")
    key_path = tmp_path / "secrets" / "master_encryption_key"
    key_path.parent.mkdir()
    key_path.write_text(f"{key}\n", encoding="ascii")
    key_path.chmod(0o644)

    resolved = load_master_encryption_key(settings_for(key_path))

    assert resolved == key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_missing_key_is_generated_once_and_survives_second_startup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First startup persists one key and later startup loads exactly that key."""
    key_path = tmp_path / "secrets" / "master_encryption_key"
    configuration = settings_for(key_path)
    caplog.set_level(logging.INFO)

    first = load_master_encryption_key(configuration)
    second = load_master_encryption_key(configuration)

    assert first == second
    assert key_path.read_text(encoding="ascii").strip() == first
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert caplog.text.count("Master encryption key generated and persisted.") == 1


def test_concurrent_bootstrap_resolves_one_authoritative_key(tmp_path: Path) -> None:
    """Concurrent API/worker processes serialize generation through the lock file."""
    key_path = tmp_path / "secrets" / "master_encryption_key"

    with ProcessPoolExecutor(max_workers=8) as executor:
        keys = list(executor.map(load_key_in_separate_process, [str(key_path)] * 16))

    assert len(set(keys)) == 1
    assert key_path.read_text(encoding="ascii").strip() == keys[0]


def test_malformed_persistent_key_fails_without_replacement(tmp_path: Path) -> None:
    """Corrupted key material is preserved for recovery instead of silently replaced."""
    key_path = tmp_path / "secrets" / "master_encryption_key"
    key_path.parent.mkdir()
    key_path.write_text("corrupted-key\n", encoding="ascii")

    with pytest.raises(MasterEncryptionKeyError, match="malformed"):
        load_master_encryption_key(settings_for(key_path))

    assert key_path.read_text(encoding="ascii") == "corrupted-key\n"


def test_malformed_environment_key_fails_before_file_fallback(tmp_path: Path) -> None:
    """An explicit invalid override is never ignored in favour of another source."""
    key_path = tmp_path / "master_encryption_key"
    key_path.write_text(Fernet.generate_key().decode("ascii"), encoding="ascii")

    with pytest.raises(MasterEncryptionKeyError, match="environment variable"):
        load_master_encryption_key(settings_for(key_path, "invalid-environment-key"))


def test_environment_and_persistent_key_mismatch_fails(tmp_path: Path) -> None:
    """Core refuses to choose silently when two valid configured sources disagree."""
    key_path = tmp_path / "master_encryption_key"
    key_path.write_text(Fernet.generate_key().decode("ascii"), encoding="ascii")
    different_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(MasterEncryptionKeyMismatchError, match="does not match"):
        load_master_encryption_key(settings_for(key_path, different_key))


def test_key_value_is_never_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Bootstrap logs only the key source and never the generated secret."""
    caplog.set_level(logging.INFO)
    key = load_master_encryption_key(settings_for(tmp_path / "master_encryption_key"))

    assert key not in caplog.text
    assert "generated and persisted" in caplog.text


def test_encryption_round_trip_survives_simulated_restart(tmp_path: Path) -> None:
    """A new cipher instance can decrypt data written before process restart."""
    configuration = settings_for(tmp_path / "secrets" / "master_encryption_key")
    first_cipher = CredentialCipher(load_master_encryption_key(configuration))
    ciphertext = first_cipher.encrypt("persistent-provider-key")

    restarted_cipher = CredentialCipher(load_master_encryption_key(configuration))

    assert restarted_cipher.decrypt(ciphertext) == "persistent-provider-key"


def test_persisted_credential_survives_restart_and_wrong_key_fails_safely(
    tmp_path: Path,
) -> None:
    """Database credentials require the matching persisted key after a restart."""
    key_configuration = settings_for(tmp_path / "secrets" / "master_encryption_key")
    database_path = tmp_path / "core.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Integration.__table__, IntegrationCredential.__table__],
    )
    key = load_master_encryption_key(key_configuration)

    with Session(engine) as session:
        user = User(
            email="admin@example.com",
            full_name="Core Admin",
            password_hash="not-used",
            is_admin=True,
        )
        session.add(user)
        session.commit()
        service = IntegrationService(session, CredentialCipher(key))
        integration = service.create(
            IntegrationCreate(
                provider=IntegrationProvider.AQSI,
                name="AQSI",
                enabled=True,
            ),
            actor_id=user.id,
        )
        service.replace_api_key(
            integration.id,
            "credential-before-restart",
            actor_id=user.id,
        )
        integration_id = integration.id

    restarted_key = load_master_encryption_key(key_configuration)
    with Session(engine) as restarted_session:
        restarted_service = IntegrationService(
            restarted_session,
            CredentialCipher(restarted_key),
        )
        assert restarted_service.decrypt_api_key(integration_id) == "credential-before-restart"
        validate_stored_integration_credentials(restarted_session, restarted_key)
        wrong_key = Fernet.generate_key().decode("ascii")
        with pytest.raises(MasterEncryptionKeyMismatchError, match="cannot decrypt"):
            validate_stored_integration_credentials(restarted_session, wrong_key)


def test_docker_compose_shares_persistent_secret_volume() -> None:
    """Deployment mounts one named secret volume into both API and worker."""
    project_root = Path(__file__).resolve().parents[1]
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")
    environment_example = (project_root / ".env.example").read_text(encoding="utf-8")

    assert compose.count("- core_secrets:/var/lib/core/secrets") == 2
    assert "  core_secrets:" in compose
    assert (
        "CORE_MASTER_ENCRYPTION_KEY_FILE=/var/lib/core/secrets/master_encryption_key"
        in environment_example
    )

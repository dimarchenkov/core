from __future__ import annotations

import logging
from collections.abc import Generator

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.config import Settings, get_settings
from core.database import get_session
from core.identity.models import PrivilegeAuditEvent, User
from core.identity.security import create_access_token
from core.identity.service import IdentityService
from core.integrations.aqsi.client import AqsiApiError
from core.integrations.aqsi.jobs import synchronize_aqsi_catalog
from core.integrations.credentials import FERNET_ALGORITHM, CredentialCipher
from core.integrations.enums import CredentialKind, IntegrationProvider, ProviderCapability
from core.integrations.models import Integration, IntegrationCredential
from core.integrations.providers import get_provider_adapter
from core.integrations.runtime import resolve_aqsi_settings
from core.integrations.schemas import IntegrationCreate, IntegrationUpdate
from core.integrations.service import IntegrationConfigurationError, IntegrationService
from core.jobs import get_default_queue
from core.main import create_app
from core.shared.db import Base

MASTER_KEY = Fernet.generate_key().decode("ascii")
JWT_SECRET = "test-only-jwt-secret-at-least-32-bytes"


@pytest.fixture
def session() -> Generator[Session]:
    """Provide integration and identity tables in an in-memory database."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            PrivilegeAuditEvent.__table__,
            Integration.__table__,
            IntegrationCredential.__table__,
        ],
    )
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def settings() -> Settings:
    """Provide runtime secrets without any real external credential."""
    return Settings(jwt_secret=JWT_SECRET, master_encryption_key=SecretStr(MASTER_KEY))


@pytest.fixture
def admin(session: Session) -> User:
    """Create an administrator allowed to manage integrations."""
    return IdentityService(session).create_admin(
        "admin@example.com",
        "Core Admin",
        "long enough password",
    )


@pytest.fixture
def operator(session: Session) -> User:
    """Create an active non-admin operator."""
    user = IdentityService(session).create_admin(
        "operator@example.com",
        "Core Operator",
        "long enough password",
    )
    user.is_admin = False
    session.commit()
    return user


@pytest.fixture
def client(session: Session, settings: Settings) -> Generator[TestClient]:
    """Provide the real settings API over the in-memory database."""
    app = create_app()

    def override_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth(user: User) -> dict[str, str]:
    """Return a valid bearer header for one test user."""
    token = create_access_token(user.id, JWT_SECRET, "HS256", 60)
    return {"Authorization": f"Bearer {token}"}


def create_aqsi(
    service: IntegrationService,
    actor: User,
    *,
    enabled: bool = True,
) -> Integration:
    """Create one AQSI integration through the application service."""
    return service.create(
        IntegrationCreate(
            provider=IntegrationProvider.AQSI,
            name="Main AQSI",
            enabled=enabled,
            configuration={"shop_id": "shop-2010", "tax_code": 6},
        ),
        actor_id=actor.id,
    )


def test_provider_registry_exposes_only_proven_aqsi_capabilities() -> None:
    """AQSI metadata does not claim spike or future operations as production capability."""
    adapter = get_provider_adapter(IntegrationProvider.AQSI)

    assert adapter.display_name == "AQSI"
    assert adapter.capabilities == frozenset(
        {
            ProviderCapability.PAYMENT,
            ProviderCapability.FISCALIZATION,
            ProviderCapability.CATALOG_PROJECTION,
        }
    )


def test_integration_is_not_a_provider_singleton(session: Session, admin: User) -> None:
    """Several provider connections can exist for future Store assignment."""
    service = IntegrationService(session)

    first = create_aqsi(service, admin)
    second = create_aqsi(service, admin)

    assert first.id != second.id
    assert len(service.list_for_provider(IntegrationProvider.AQSI)) == 2


def test_api_key_is_encrypted_at_rest_and_never_in_read_model(
    session: Session,
    settings: Settings,
    admin: User,
) -> None:
    """Only authenticated ciphertext is persisted and safe reads omit secret fields."""
    secret = "aqsi-super-secret-value"
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)

    credential = service.replace_api_key(integration.id, secret, actor_id=admin.id)
    read = service.read(integration).model_dump(mode="json")

    assert credential.secret_ciphertext != secret
    assert secret not in credential.secret_ciphertext
    assert credential.encryption_algorithm == FERNET_ALGORITHM
    assert service.decrypt_api_key(integration.id) == secret
    assert "api_key" not in read
    assert "secret_ciphertext" not in read
    assert secret not in str(read)


def test_admin_can_replace_credential_and_enable_integration(
    session: Session,
    settings: Settings,
    admin: User,
) -> None:
    """Credential rotation replaces ciphertext and records rotation metadata."""
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin, enabled=False)
    first = service.replace_api_key(integration.id, "first-key", actor_id=admin.id)
    first_ciphertext = first.secret_ciphertext

    service.replace_api_key(integration.id, "second-key", actor_id=admin.id)
    updated = service.update(
        integration.id,
        IntegrationUpdate(enabled=True),
        actor_id=admin.id,
    )
    credential = session.query(IntegrationCredential).one()

    assert service.decrypt_api_key(integration.id) == "second-key"
    assert credential.secret_ciphertext != first_ciphertext
    assert credential.rotated_at is not None
    assert updated.enabled is True


def test_non_admin_cannot_read_or_manage_integration_credentials(
    client: TestClient,
    operator: User,
) -> None:
    """The smallest current authorization boundary denies all Settings integration APIs."""
    headers = auth(operator)

    assert client.get("/api/settings/integrations", headers=headers).status_code == 403
    assert (
        client.put(
            "/api/settings/integrations/00000000-0000-0000-0000-000000000001/credential",
            headers=headers,
            json={"api_key": "must-not-be-accepted"},
        ).status_code
        == 403
    )


def test_normal_api_never_returns_plaintext_key(
    client: TestClient,
    session: Session,
    settings: Settings,
    admin: User,
) -> None:
    """HTTP read projections expose only credential timestamps."""
    secret = "browser-must-never-see-this"
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)
    service.replace_api_key(integration.id, secret, actor_id=admin.id)

    response = client.get("/api/settings/integrations", headers=auth(admin))

    assert response.status_code == 200
    assert response.json()[0]["credential_saved_at"] is not None
    assert secret not in response.text
    assert "secret_ciphertext" not in response.text


def test_settings_backed_aqsi_precedes_legacy_environment(
    session: Session,
    settings: Settings,
    admin: User,
) -> None:
    """Persisted configuration and decrypted key override legacy env values."""
    legacy = settings.model_copy(
        update={
            "aqsi_enabled": False,
            "aqsi_api_key": SecretStr("legacy-key"),
            "aqsi_shop_id": "legacy-shop",
        }
    )
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)
    service.replace_api_key(integration.id, "database-key", actor_id=admin.id)

    resolved, using_legacy = resolve_aqsi_settings(session, legacy)

    assert using_legacy is False
    assert resolved.aqsi_enabled is True
    assert resolved.aqsi_api_key is not None
    assert resolved.aqsi_api_key.get_secret_value() == "database-key"
    assert resolved.aqsi_shop_id == "shop-2010"


def test_legacy_environment_remains_fallback_without_integration(
    session: Session,
    settings: Settings,
) -> None:
    """A deployment remains operational before an admin performs migration."""
    legacy = settings.model_copy(
        update={"aqsi_enabled": True, "aqsi_api_key": SecretStr("legacy-key")}
    )

    resolved, using_legacy = resolve_aqsi_settings(session, legacy)

    assert using_legacy is True
    assert resolved.aqsi_api_key is not None
    assert resolved.aqsi_api_key.get_secret_value() == "legacy-key"


def test_non_secret_external_ids_persist_in_configuration(
    session: Session,
    admin: User,
) -> None:
    """AQSI shop/device identifiers remain ordinary non-secret provider configuration."""
    service = IntegrationService(session)
    integration = create_aqsi(service, admin)

    updated = service.update(
        integration.id,
        IntegrationUpdate(
            configuration={"shop_id": "shop-b", "device_id": "device-7", "tax_code": 6}
        ),
        actor_id=admin.id,
    )

    assert updated.configuration["shop_id"] == "shop-b"
    assert updated.configuration["device_id"] == "device-7"


def test_aqsi_catalog_sync_configuration_requires_boolean(
    session: Session,
    admin: User,
) -> None:
    """The automatic synchronization switch cannot be stored as an ambiguous string."""
    service = IntegrationService(session)
    integration = create_aqsi(service, admin)

    updated = service.update(
        integration.id,
        IntegrationUpdate(
            configuration={
                **integration.configuration,
                "catalog_sync_enabled": True,
            }
        ),
        actor_id=admin.id,
    )

    assert updated.configuration["catalog_sync_enabled"] is True
    with pytest.raises(IntegrationConfigurationError, match="must be a boolean"):
        service.update(
            integration.id,
            IntegrationUpdate(
                configuration={
                    **updated.configuration,
                    "catalog_sync_enabled": "yes",
                }
            ),
            actor_id=admin.id,
        )


def test_admin_can_queue_full_aqsi_catalog_sync(
    session: Session,
    settings: Settings,
    admin: User,
) -> None:
    """Synchronize-now is asynchronous, attributed, and restricted to configured AQSI."""
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)
    service.replace_api_key(integration.id, "server-side-key", actor_id=admin.id)
    app = create_app()

    class FakeQueue:
        def __init__(self) -> None:
            self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

        def enqueue(self, function: object, *args: object, **kwargs: object) -> None:
            self.calls.append((function, args, kwargs))

    queue = FakeQueue()

    def override_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_default_queue] = lambda: queue
    with TestClient(app) as test_client:
        response = test_client.post(
            f"/api/settings/integrations/{integration.id}/sync",
            headers=auth(admin),
        )
    app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["queued"] is True
    assert len(queue.calls) == 1
    function, args, options = queue.calls[0]
    assert function is synchronize_aqsi_catalog
    assert args == (str(admin.id), True)
    assert options["job_timeout"] == 300


def test_connection_test_decrypts_server_side_without_leaking_secret(
    client: TestClient,
    session: Session,
    settings: Settings,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The browser triggers a harmless read while plaintext stays inside the adapter call."""
    secret = "server-side-only-key"
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)
    service.replace_api_key(integration.id, secret, actor_id=admin.id)
    observed: list[str] = []

    class FakeClient:
        def __init__(self, runtime: Settings) -> None:
            assert runtime.aqsi_api_key is not None
            observed.append(runtime.aqsi_api_key.get_secret_value())

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def list_shop_ids(self) -> list[str]:
            return ["shop-2010"]

    monkeypatch.setattr("core.integrations.routes.AqsiHttpClient", FakeClient)

    response = client.post(
        f"/api/settings/integrations/{integration.id}/test",
        headers=auth(admin),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "Подключение успешно"}
    assert observed == [secret]
    assert secret not in response.text


def test_connection_failure_response_and_log_never_include_secret(
    client: TestClient,
    session: Session,
    settings: Settings,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Even a provider error echoing the key is reduced to safe code and copy."""
    secret = "provider-echoed-secret"
    service = IntegrationService(session, CredentialCipher.from_settings(settings))
    integration = create_aqsi(service, admin)
    service.replace_api_key(integration.id, secret, actor_id=admin.id)

    class RejectingClient:
        def __init__(self, runtime: Settings) -> None:
            del runtime

        def __enter__(self) -> RejectingClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def list_shop_ids(self) -> list[str]:
            raise AqsiApiError("http_401", f"Rejected {secret}")

    monkeypatch.setattr("core.integrations.routes.AqsiHttpClient", RejectingClient)
    caplog.set_level(logging.WARNING)

    response = client.post(
        f"/api/settings/integrations/{integration.id}/test",
        headers=auth(admin),
    )

    assert response.json() == {"ok": False, "message": "API key отклонён AQSI"}
    assert secret not in response.text
    assert secret not in caplog.text


def test_credential_kind_has_no_plaintext_database_column() -> None:
    """The persistence schema cannot accidentally map a normal plaintext secret field."""
    columns = set(IntegrationCredential.__table__.columns.keys())

    assert "secret_ciphertext" in columns
    assert "api_key" not in columns
    assert "secret" not in columns
    assert CredentialKind.API_KEY.value == "api_key"


def test_plaintext_key_is_rejected_from_non_secret_configuration(
    client: TestClient,
    admin: User,
) -> None:
    """The generic JSON configuration cannot become a plaintext secret escape hatch."""
    response = client.post(
        "/api/settings/integrations",
        headers=auth(admin),
        json={
            "provider": "aqsi",
            "name": "Unsafe AQSI",
            "enabled": True,
            "configuration": {"api_key": "plaintext-is-forbidden"},
        },
    )

    assert response.status_code == 422
    assert "plaintext-is-forbidden" not in response.text

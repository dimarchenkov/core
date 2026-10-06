from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.integrations.credentials import FERNET_ALGORITHM, CredentialCipher
from core.integrations.enums import CredentialKind, IntegrationProvider
from core.integrations.models import Integration, IntegrationCredential
from core.integrations.providers import get_provider_adapter
from core.integrations.repository import IntegrationRepository
from core.integrations.schemas import IntegrationCreate, IntegrationRead, IntegrationUpdate
from core.shared.db import UUIDv7

AQSI_CONFIGURATION_KEYS = frozenset(
    {
        "shop_id",
        "device_id",
        "tax_code",
        "tax_system_code",
        "default_group_id",
        "default_group_name",
        "catalog_sync_enabled",
    }
)


class IntegrationNotFoundError(Exception):
    """Raised when a requested integration does not exist."""


class IntegrationConfigurationError(Exception):
    """Raised when a provider configuration cannot be used safely."""


class IntegrationService:
    """Administrative workflows for provider connections and secret rotation."""

    def __init__(self, session: Session, cipher: CredentialCipher | None = None) -> None:
        """Create a service using persistence and infrastructure-owned encryption."""
        self._session = session
        self._cipher = cipher
        self._integrations = IntegrationRepository(session)

    def create(self, data: IntegrationCreate, *, actor_id: UUIDv7) -> Integration:
        """Create one independent provider connection."""
        get_provider_adapter(data.provider)
        configuration = self._validate_configuration(data.provider, data.configuration)
        integration = Integration(
            provider=data.provider,
            name=data.name.strip(),
            enabled=data.enabled,
            configuration=configuration,
            created_by_id=actor_id,
        )
        self._integrations.add(integration)
        self._session.commit()
        self._session.refresh(integration)
        return integration

    def update(
        self,
        integration_id: UUIDv7,
        data: IntegrationUpdate,
        *,
        actor_id: UUIDv7,
    ) -> Integration:
        """Update enabled state or non-secret provider configuration."""
        integration = self._require(integration_id, for_update=True)
        if data.name is not None:
            integration.name = data.name.strip()
        if data.enabled is not None:
            integration.enabled = data.enabled
        if data.configuration is not None:
            integration.configuration = self._validate_configuration(
                integration.provider,
                data.configuration,
            )
        integration.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(integration)
        return integration

    def replace_api_key(
        self,
        integration_id: UUIDv7,
        api_key: str,
        *,
        actor_id: UUIDv7,
    ) -> IntegrationCredential:
        """Encrypt and atomically store or rotate a provider API key."""
        normalized = api_key.strip()
        if not normalized:
            raise ValueError("API key must not be empty.")
        integration = self._require(integration_id, for_update=True)
        credential = self._integrations.get_credential(
            integration.id,
            CredentialKind.API_KEY,
            for_update=True,
        )
        now = datetime.now(UTC)
        if self._cipher is None:
            raise IntegrationConfigurationError("Master encryption key is not configured.")
        ciphertext = self._cipher.encrypt(normalized)
        if credential is None:
            credential = IntegrationCredential(
                integration_id=integration.id,
                kind=CredentialKind.API_KEY,
                secret_ciphertext=ciphertext,
                encryption_algorithm=FERNET_ALGORITHM,
                key_version=self._cipher.key_version,
                created_by_id=actor_id,
            )
            self._integrations.add_credential(credential)
        else:
            credential.secret_ciphertext = ciphertext
            credential.encryption_algorithm = FERNET_ALGORITHM
            credential.key_version = self._cipher.key_version
            credential.rotated_at = now
            credential.updated_by_id = actor_id
        integration.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def decrypt_api_key(self, integration_id: UUIDv7) -> str:
        """Decrypt an API key transiently for a server-side provider call."""
        self._require(integration_id)
        credential = self._integrations.get_credential(integration_id, CredentialKind.API_KEY)
        if credential is None:
            raise IntegrationConfigurationError("API key is not configured.")
        if self._cipher is None:
            raise IntegrationConfigurationError("Master encryption key is not configured.")
        return self._cipher.decrypt(credential.secret_ciphertext)

    def read(self, integration: Integration) -> IntegrationRead:
        """Build a projection that cannot serialize stored secret material."""
        credential = self._integrations.get_credential(integration.id, CredentialKind.API_KEY)
        adapter = get_provider_adapter(integration.provider)
        return IntegrationRead(
            id=integration.id,
            provider=integration.provider,
            provider_name=adapter.display_name,
            name=integration.name,
            enabled=integration.enabled,
            configuration=dict(integration.configuration),
            capabilities=sorted(adapter.capabilities, key=str),
            credential_saved_at=credential.created_at if credential is not None else None,
            credential_rotated_at=credential.rotated_at if credential is not None else None,
            created_at=integration.created_at,
            updated_at=integration.updated_at,
        )

    def list_for_provider(self, provider: IntegrationProvider) -> list[Integration]:
        """Return every active connection for a provider without singleton assumptions."""
        return list(self._integrations.list_for_provider(provider))

    def get(self, integration_id: UUIDv7) -> Integration:
        """Return one active provider connection or raise a domain error."""
        return self._require(integration_id)

    def _require(self, integration_id: UUIDv7, *, for_update: bool = False) -> Integration:
        integration = self._integrations.get(integration_id, for_update=for_update)
        if integration is None:
            raise IntegrationNotFoundError
        return integration

    @staticmethod
    def _validate_configuration(
        provider: IntegrationProvider,
        configuration: dict[str, object],
    ) -> dict[str, object]:
        """Reject secrets and unknown fields from provider non-secret configuration."""
        if provider is IntegrationProvider.AQSI:
            unknown = set(configuration) - AQSI_CONFIGURATION_KEYS
            if unknown:
                names = ", ".join(sorted(unknown))
                raise IntegrationConfigurationError(
                    f"Unsupported non-secret AQSI configuration fields: {names}."
                )
            catalog_sync_enabled = configuration.get("catalog_sync_enabled")
            if catalog_sync_enabled is not None and not isinstance(catalog_sync_enabled, bool):
                raise IntegrationConfigurationError(
                    "AQSI catalog_sync_enabled must be a boolean."
                )
        return dict(configuration)

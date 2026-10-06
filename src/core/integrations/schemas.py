from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.integrations.enums import IntegrationProvider, ProviderCapability
from core.shared.db import UUIDv7


class IntegrationCreate(BaseModel):
    """Administrative command for creating a provider connection."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: IntegrationProvider
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = False
    configuration: dict[str, object] = Field(default_factory=dict)


class IntegrationUpdate(BaseModel):
    """Editable non-secret integration settings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    configuration: dict[str, object] | None = None


class CredentialReplace(BaseModel):
    """Write-only API-key replacement command."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    api_key: str = Field(min_length=1, max_length=4096)


class IntegrationRead(BaseModel):
    """Safe integration projection that intentionally omits ciphertext and plaintext."""

    id: UUIDv7
    provider: IntegrationProvider
    provider_name: str
    name: str
    enabled: bool
    configuration: dict[str, object]
    capabilities: list[ProviderCapability]
    credential_saved_at: datetime | None
    credential_rotated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AqsiSettingsRead(BaseModel):
    """Operator-facing AQSI state including safe legacy migration status."""

    integration: IntegrationRead | None
    status: str
    using_legacy_environment: bool
    legacy_available: bool


class ConnectionTestRead(BaseModel):
    """Sanitized result of a harmless provider connection check."""

    ok: bool
    message: str


class AqsiShopRead(BaseModel):
    """One discoverable AQSI shop supported by the current adapter."""

    id: str
    name: str


class CatalogSyncRequestRead(BaseModel):
    """Acknowledgement that an administrative catalog sweep was queued."""

    queued: bool
    message: str

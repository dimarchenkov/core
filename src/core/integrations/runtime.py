from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.orm import Session

from core.config import Settings
from core.integrations.credentials import CredentialCipher
from core.integrations.enums import CredentialKind, IntegrationProvider
from core.integrations.models import Integration
from core.integrations.repository import IntegrationRepository


class AmbiguousIntegrationError(Exception):
    """Raised when a workflow cannot safely choose among several connections."""


def get_aqsi_integration(session: Session) -> Integration | None:
    """Resolve the sole current AQSI connection without treating it as a singleton."""
    integrations = list(IntegrationRepository(session).list_for_provider(IntegrationProvider.AQSI))
    if not integrations:
        return None
    if len(integrations) > 1:
        raise AmbiguousIntegrationError(
            "Several AQSI integrations exist; a future Store assignment is required."
        )
    return integrations[0]


def resolve_aqsi_settings(session: Session, settings: Settings) -> tuple[Settings, bool]:
    """Prefer Settings-backed AQSI data, otherwise preserve legacy environment fallback."""
    integration = get_aqsi_integration(session)
    if integration is None:
        return settings, bool(settings.aqsi_api_key)

    repository = IntegrationRepository(session)
    credential = repository.get_credential(integration.id, CredentialKind.API_KEY)
    api_key: SecretStr | None = None
    if credential is not None:
        cipher = CredentialCipher.from_settings(settings)
        api_key = SecretStr(cipher.decrypt(credential.secret_ciphertext))
    configuration = integration.configuration
    shop_id = configuration.get("shop_id")
    device_id = configuration.get("device_id")
    tax_code = configuration.get("tax_code", settings.aqsi_tax_code)
    tax_system_code = configuration.get(
        "tax_system_code", settings.aqsi_sale_spike_tax_system_code
    )
    return (
        settings.model_copy(
            update={
                "aqsi_enabled": integration.enabled,
                "aqsi_api_key": api_key,
                "aqsi_shop_id": str(shop_id) if shop_id else None,
                "aqsi_tax_code": int(tax_code),
                "aqsi_default_group_id": str(
                    configuration.get("default_group_id", settings.aqsi_default_group_id)
                ),
                "aqsi_default_group_name": str(
                    configuration.get("default_group_name", settings.aqsi_default_group_name)
                ),
                "aqsi_sale_spike_device_id": str(device_id) if device_id else None,
                "aqsi_sale_spike_tax_system_code": (
                    int(tax_system_code) if tax_system_code is not None else None
                ),
            }
        ),
        False,
    )

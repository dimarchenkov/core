from __future__ import annotations

from dataclasses import dataclass

from core.integrations.enums import IntegrationProvider, ProviderCapability


@dataclass(frozen=True)
class ProviderAdapter:
    """Static provider metadata kept outside persistence and Catalog."""

    provider: IntegrationProvider
    display_name: str
    capabilities: frozenset[ProviderCapability]


AQSI_ADAPTER = ProviderAdapter(
    provider=IntegrationProvider.AQSI,
    display_name="AQSI",
    capabilities=frozenset(
        {
            ProviderCapability.PAYMENT,
            ProviderCapability.FISCALIZATION,
            ProviderCapability.CATALOG_PROJECTION,
        }
    ),
)

PROVIDER_ADAPTERS: dict[IntegrationProvider, ProviderAdapter] = {
    AQSI_ADAPTER.provider: AQSI_ADAPTER,
}


def get_provider_adapter(provider: IntegrationProvider) -> ProviderAdapter:
    """Return registered metadata for a supported provider."""
    return PROVIDER_ADAPTERS[provider]

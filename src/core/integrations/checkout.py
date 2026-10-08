from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.orm import Session

from core.config import Settings
from core.integrations.aqsi.checkout import AqsiCheckoutAdapter
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.credentials import CredentialCipher, CredentialDecryptionError
from core.integrations.enums import CredentialKind, IntegrationProvider, ProviderCapability
from core.integrations.master_key import MasterEncryptionKeyError
from core.integrations.providers import get_provider_adapter
from core.integrations.repository import IntegrationRepository
from core.sales.providers import CheckoutProviderBinding
from core.shared.db import UUIDv7


class CheckoutIntegrationUnavailableError(Exception):
    """Raised when no single safe payment/fiscal Integration can be resolved."""


class CheckoutProviderFactory:
    """Composition root resolving generic checkout providers from Integration settings."""

    def __init__(self, session: Session, settings: Settings) -> None:
        """Create a factory without reading legacy provider environment credentials."""
        self._session = session
        self._settings = settings
        self._integrations = IntegrationRepository(session)

    def for_new_checkout(self) -> CheckoutProviderBinding:
        """Resolve the sole enabled Integration supporting payment and fiscalization."""
        candidates = []
        for provider in IntegrationProvider:
            adapter = get_provider_adapter(provider)
            required = {ProviderCapability.PAYMENT, ProviderCapability.FISCALIZATION}
            if not required.issubset(adapter.capabilities):
                continue
            candidates.extend(
                integration
                for integration in self._integrations.list_for_provider(provider)
                if integration.enabled
            )
        if not candidates:
            raise CheckoutIntegrationUnavailableError("Оплата не настроена")
        if len(candidates) > 1:
            raise CheckoutIntegrationUnavailableError(
                "Настроено несколько касс; выбор кассы для точки продаж ещё не задан"
            )
        return self._build(candidates[0].id, require_enabled=True)

    def for_recovery(self, integration_id: UUIDv7) -> CheckoutProviderBinding:
        """Resolve the exact persisted Integration even if new checkout was disabled later."""
        return self._build(integration_id, require_enabled=False)

    def _build(self, integration_id: UUIDv7, *, require_enabled: bool) -> CheckoutProviderBinding:
        integration = self._integrations.get(integration_id)
        if integration is None or (require_enabled and not integration.enabled):
            raise CheckoutIntegrationUnavailableError("Оплата не настроена")
        if integration.provider is not IntegrationProvider.AQSI:
            raise CheckoutIntegrationUnavailableError("Провайдер оплаты не поддерживается")
        credential = self._integrations.get_credential(integration.id, CredentialKind.API_KEY)
        if credential is None:
            raise CheckoutIntegrationUnavailableError("Не сохранён ключ подключения AQSI")
        configuration = integration.configuration
        device_id = configuration.get("device_id")
        tax_system_code = configuration.get("tax_system_code")
        tax_code = configuration.get("tax_code")
        acquiring_mode = configuration.get("acquiring_mode", "sbp_with_card")
        try:
            numeric_device_id = int(str(device_id or ""))
        except ValueError as exc:
            raise CheckoutIntegrationUnavailableError("Не настроено устройство AQSI") from exc
        if numeric_device_id <= 0:
            raise CheckoutIntegrationUnavailableError("Не настроено устройство AQSI")
        if tax_system_code not in {1, 2, 4, 16, 32}:
            raise CheckoutIntegrationUnavailableError("Не настроена система налогообложения AQSI")
        if not isinstance(tax_code, int) or not 1 <= tax_code <= 10:
            raise CheckoutIntegrationUnavailableError("Не настроена ставка НДС AQSI")
        if acquiring_mode not in {"card_only", "sbp_with_card", "sbp_only"}:
            raise CheckoutIntegrationUnavailableError("Некорректный режим эквайринга AQSI")
        try:
            api_key = CredentialCipher.from_settings(self._settings).decrypt(
                credential.secret_ciphertext
            )
        except (MasterEncryptionKeyError, CredentialDecryptionError) as exc:
            raise CheckoutIntegrationUnavailableError("Хранилище ключа AQSI недоступно") from exc
        runtime = self._settings.model_copy(
            update={
                "aqsi_enabled": True,
                "aqsi_api_key": SecretStr(api_key),
                "aqsi_sale_spike_device_id": str(numeric_device_id),
                "aqsi_sale_spike_tax_system_code": int(tax_system_code),
                "aqsi_tax_code": tax_code,
                "aqsi_acquiring_mode": acquiring_mode,
            }
        )
        client = AqsiHttpClient(runtime)
        aqsi = AqsiCheckoutAdapter(runtime, client)
        return CheckoutProviderBinding(
            integration_id=integration.id,
            integration_name=integration.name,
            provider=integration.provider,
            provider_display_name=get_provider_adapter(integration.provider).display_name,
            payment_display_name=aqsi.payment_display_name,
            payment=aqsi,
            fiscal=aqsi,
            close=client.close,
        )

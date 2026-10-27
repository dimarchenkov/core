from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.orm import Session

from core.config import Settings
from core.integrations.aqsi.checkout import AqsiCheckoutAdapter
from core.integrations.aqsi.client import AqsiHttpClient
from core.integrations.credentials import CredentialCipher, CredentialDecryptionError
from core.integrations.enums import CredentialKind, IntegrationProvider, ProviderCapability
from core.integrations.master_key import MasterEncryptionKeyError
from core.integrations.models import Integration
from core.integrations.providers import get_provider_adapter
from core.integrations.repository import IntegrationRepository
from core.sales.providers import CheckoutProviderBinding
from core.shared.db import UUIDv7


class CheckoutIntegrationUnavailableError(Exception):
    """Raised when the requested checkout capabilities cannot be resolved safely."""


@dataclass(frozen=True)
class _ProviderRuntime:
    """One explicitly registered Integration adapter and its lifecycle hook."""

    integration: Integration
    adapter: AqsiCheckoutAdapter
    display_name: str
    close: Callable[[], None]


class CheckoutProviderFactory:
    """Composition root selecting payment and fiscal providers independently."""

    _CAPABILITY_FLAGS = {
        ProviderCapability.PAYMENT: "payment_enabled",
        ProviderCapability.FISCALIZATION: "fiscalization_enabled",
    }

    def __init__(self, session: Session, settings: Settings) -> None:
        """Create a factory without reading legacy provider environment credentials."""
        self._session = session
        self._settings = settings
        self._integrations = IntegrationRepository(session)

    def for_new_checkout(self) -> CheckoutProviderBinding:
        """Resolve independently configured payment and fiscal capabilities."""
        payment = self._select(ProviderCapability.PAYMENT)
        fiscal = self._select(ProviderCapability.FISCALIZATION)
        return self._build_binding(
            payment_integration_id=payment.id,
            fiscal_integration_id=fiscal.id,
            require_enabled=True,
        )

    def for_health_check(self) -> CheckoutProviderBinding:
        """Resolve every configured capability without requiring both to exist."""
        payment = self._select_optional(ProviderCapability.PAYMENT)
        fiscal = self._select_optional(ProviderCapability.FISCALIZATION)
        if payment is None and fiscal is None:
            raise CheckoutIntegrationUnavailableError("Оплата и фискализация не настроены")
        return self._build_binding(
            payment_integration_id=payment.id if payment is not None else None,
            fiscal_integration_id=fiscal.id if fiscal is not None else None,
            require_enabled=True,
        )

    def for_recovery(
        self,
        integration_id: UUIDv7 | None = None,
        *,
        payment_integration_id: UUIDv7 | None = None,
        fiscal_integration_id: UUIDv7 | None = None,
    ) -> CheckoutProviderBinding:
        """Resolve exact persisted selections even when later disabled.

        ``integration_id`` is the backward-compatible form for historical rows
        where one AQSI Integration supplied both capabilities.
        """
        if integration_id is not None:
            payment_integration_id = payment_integration_id or integration_id
            fiscal_integration_id = fiscal_integration_id or integration_id
        if payment_integration_id is None and fiscal_integration_id is None:
            raise CheckoutIntegrationUnavailableError("Не найден провайдер операции")
        return self._build_binding(
            payment_integration_id=payment_integration_id,
            fiscal_integration_id=fiscal_integration_id,
            require_enabled=False,
        )

    def _select(self, capability: ProviderCapability) -> Integration:
        """Select the sole enabled Integration assigned to one capability."""
        selected = self._select_optional(capability)
        if selected is not None:
            return selected
        message = (
            "Оплата не настроена"
            if capability is ProviderCapability.PAYMENT
            else "Фискализация не настроена"
        )
        raise CheckoutIntegrationUnavailableError(message)

    def _select_optional(self, capability: ProviderCapability) -> Integration | None:
        """Resolve one capability or return None when it is not configured."""
        flag = self._CAPABILITY_FLAGS[capability]
        candidates: list[Integration] = []
        for provider in IntegrationProvider:
            adapter = get_provider_adapter(provider)
            if capability not in adapter.capabilities:
                continue
            candidates.extend(
                integration
                for integration in self._integrations.list_for_provider(provider)
                if integration.enabled and integration.configuration.get(flag, True) is not False
            )
        if len(candidates) > 1:
            role = "оплаты" if capability is ProviderCapability.PAYMENT else "фискализации"
            raise CheckoutIntegrationUnavailableError(
                f"Настроено несколько провайдеров {role}; выберите один в конфигурации"
            )
        return candidates[0] if candidates else None

    def _build_binding(
        self,
        *,
        payment_integration_id: UUIDv7 | None,
        fiscal_integration_id: UUIDv7 | None,
        require_enabled: bool,
    ) -> CheckoutProviderBinding:
        """Build one checkout binding from one or two persisted selections."""
        if payment_integration_id == fiscal_integration_id and payment_integration_id is not None:
            shared = self._build_runtime(
                payment_integration_id,
                required={ProviderCapability.PAYMENT, ProviderCapability.FISCALIZATION},
                require_enabled=require_enabled,
            )
            payment_runtime = fiscal_runtime = shared
        else:
            payment_runtime = (
                self._build_runtime(
                    payment_integration_id,
                    required={ProviderCapability.PAYMENT},
                    require_enabled=require_enabled,
                )
                if payment_integration_id is not None
                else None
            )
            fiscal_runtime = (
                self._build_runtime(
                    fiscal_integration_id,
                    required={ProviderCapability.FISCALIZATION},
                    require_enabled=require_enabled,
                )
                if fiscal_integration_id is not None
                else None
            )
        primary = payment_runtime or fiscal_runtime
        if primary is None:
            raise CheckoutIntegrationUnavailableError("Не найден провайдер операции")

        runtimes = [item for item in (payment_runtime, fiscal_runtime) if item is not None]

        def close() -> None:
            seen: set[int] = set()
            for runtime in runtimes:
                identity = id(runtime.adapter)
                if identity not in seen:
                    runtime.close()
                    seen.add(identity)

        return CheckoutProviderBinding(
            integration_id=primary.integration.id,
            integration_name=primary.integration.name,
            provider=primary.integration.provider,
            provider_display_name=primary.display_name,
            payment_display_name=(
                payment_runtime.adapter.payment_display_name
                if payment_runtime is not None
                else "Оплата"
            ),
            payment=payment_runtime.adapter if payment_runtime is not None else None,
            fiscal=fiscal_runtime.adapter if fiscal_runtime is not None else None,
            close=close,
            fiscal_integration_id=(
                fiscal_runtime.integration.id if fiscal_runtime is not None else None
            ),
            fiscal_integration_name=(
                fiscal_runtime.integration.name if fiscal_runtime is not None else None
            ),
            fiscal_provider_kind=(
                fiscal_runtime.integration.provider if fiscal_runtime is not None else None
            ),
            fiscal_provider_display_name=(
                fiscal_runtime.display_name if fiscal_runtime is not None else None
            ),
        )

    def _build_runtime(
        self,
        integration_id: UUIDv7,
        *,
        required: set[ProviderCapability],
        require_enabled: bool,
    ) -> _ProviderRuntime:
        """Build one adapter through the small explicit provider registry."""
        integration = self._integrations.get(integration_id)
        if integration is None or (require_enabled and not integration.enabled):
            raise CheckoutIntegrationUnavailableError("Провайдер операции не настроен")
        descriptor = get_provider_adapter(integration.provider)
        if not required.issubset(descriptor.capabilities):
            raise CheckoutIntegrationUnavailableError(
                "Провайдер не поддерживает требуемую операцию"
            )
        if integration.provider is not IntegrationProvider.AQSI:
            raise CheckoutIntegrationUnavailableError("Провайдер операции не поддерживается")
        return self._build_aqsi(
            integration,
            required=required,
            display_name=descriptor.display_name,
        )

    def _build_aqsi(
        self,
        integration: Integration,
        *,
        required: set[ProviderCapability],
        display_name: str,
    ) -> _ProviderRuntime:
        """Build the explicitly registered AQSI adapter for requested capabilities."""
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
        if ProviderCapability.FISCALIZATION in required:
            if tax_system_code not in {1, 2, 4, 16, 32}:
                raise CheckoutIntegrationUnavailableError(
                    "Не настроена система налогообложения AQSI"
                )
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
                "aqsi_sale_spike_tax_system_code": int(tax_system_code or 0),
                "aqsi_tax_code": int(tax_code or self._settings.aqsi_tax_code),
                "aqsi_acquiring_mode": acquiring_mode,
            }
        )
        client = AqsiHttpClient(runtime)
        adapter = AqsiCheckoutAdapter(runtime, client)
        return _ProviderRuntime(integration, adapter, display_name, client.close)

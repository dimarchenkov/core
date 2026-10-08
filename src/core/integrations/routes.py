from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import SecretStr
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy.orm import Session

from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_admin
from core.identity.models import User
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.integrations.aqsi.jobs import synchronize_aqsi_catalog
from core.integrations.credentials import (
    CredentialCipher,
    CredentialDecryptionError,
)
from core.integrations.enums import IntegrationProvider
from core.integrations.master_key import MasterEncryptionKeyError
from core.integrations.models import Integration
from core.integrations.runtime import AmbiguousIntegrationError, get_aqsi_integration
from core.integrations.schemas import (
    AqsiSettingsRead,
    AqsiShopRead,
    CatalogSyncRequestRead,
    ConnectionTestRead,
    CredentialReplace,
    IntegrationCreate,
    IntegrationRead,
    IntegrationUpdate,
)
from core.integrations.service import (
    IntegrationConfigurationError,
    IntegrationNotFoundError,
    IntegrationService,
)
from core.jobs import get_default_queue
from core.shared.db import UUIDv7

logger = logging.getLogger(__name__)

SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "not_configured",
        "connect_timeout",
        "connect_error",
        "timeout",
        "network_error",
        "invalid_response",
        "unauthorized",
        "forbidden",
    }
)

router = APIRouter(
    prefix="/api/settings/integrations",
    tags=["settings", "integrations"],
    dependencies=[Depends(get_current_admin)],
)


def _service(session: Session, settings: Settings, *, require_cipher: bool) -> IntegrationService:
    """Build integration workflows and require the master key only for secret use."""
    cipher = CredentialCipher.from_settings(settings) if require_cipher else None
    return IntegrationService(session, cipher)


def _not_found(exc: Exception) -> HTTPException:
    """Return a consistent missing-integration response."""
    return HTTPException(status.HTTP_404_NOT_FOUND, "Integration not found.")


def _master_key_problem(exc: Exception) -> HTTPException:
    """Return a safe infrastructure configuration failure."""
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Шифрование секретов не настроено. Обратитесь к администратору сервера.",
    )


def _configuration_problem(exc: IntegrationConfigurationError) -> HTTPException:
    """Return a safe validation response for non-secret provider settings."""
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _safe_provider_error_code(code: str) -> str:
    """Reduce provider-controlled error codes to a non-sensitive diagnostic value."""
    if code in SAFE_PROVIDER_ERROR_CODES:
        return code
    http_status = code.removeprefix("http_")
    if code.startswith("http_") and len(http_status) == 3 and http_status.isdigit():
        return code
    return "provider_error"


def _aqsi_runtime_settings(
    integration: Integration,
    service: IntegrationService,
    settings: Settings,
) -> Settings:
    """Build transient AQSI settings from encrypted credentials and safe configuration."""
    api_key = service.decrypt_api_key(integration.id)
    configuration = integration.configuration
    shop_id = configuration.get("shop_id")
    tax_code = configuration.get("tax_code", settings.aqsi_tax_code)
    return settings.model_copy(
        update={
            "aqsi_enabled": integration.enabled,
            "aqsi_api_key": SecretStr(api_key),
            "aqsi_shop_id": str(shop_id) if shop_id else None,
            "aqsi_tax_code": int(tax_code),
        }
    )


@router.get("", response_model=list[IntegrationRead])
def list_integrations(
    session: Annotated[Session, Depends(get_session)],
) -> list[IntegrationRead]:
    """List safe provider settings for administrators."""
    service = IntegrationService(session)
    integrations: list[Integration] = []
    for provider in IntegrationProvider:
        integrations.extend(service.list_for_provider(provider))
    return [service.read(integration) for integration in integrations]


@router.post("", response_model=IntegrationRead, status_code=status.HTTP_201_CREATED)
def create_integration(
    data: IntegrationCreate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> IntegrationRead:
    """Create a provider connection without making the provider globally unique."""
    service = IntegrationService(session)
    try:
        integration = service.create(data, actor_id=current_user.id)
    except IntegrationConfigurationError as exc:
        raise _configuration_problem(exc) from exc
    return service.read(integration)


@router.patch("/{integration_id}", response_model=IntegrationRead)
def update_integration(
    integration_id: UUIDv7,
    data: IntegrationUpdate,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> IntegrationRead:
    """Update enabled state and non-secret settings."""
    service = IntegrationService(session)
    try:
        integration = service.update(integration_id, data, actor_id=current_user.id)
    except IntegrationNotFoundError as exc:
        raise _not_found(exc) from exc
    except IntegrationConfigurationError as exc:
        raise _configuration_problem(exc) from exc
    return service.read(integration)


@router.put("/{integration_id}/credential", response_model=IntegrationRead)
def replace_credential(
    integration_id: UUIDv7,
    data: CredentialReplace,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> IntegrationRead:
    """Encrypt and store an API key without ever returning it to the browser."""
    try:
        service = _service(session, settings, require_cipher=True)
        service.replace_api_key(integration_id, data.api_key, actor_id=current_user.id)
        integration = service.get(integration_id)
    except IntegrationNotFoundError as exc:
        raise _not_found(exc) from exc
    except (MasterEncryptionKeyError, IntegrationConfigurationError) as exc:
        raise _master_key_problem(exc) from exc
    return service.read(integration)


@router.get("/aqsi", response_model=AqsiSettingsRead)
def read_aqsi_settings(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AqsiSettingsRead:
    """Return safe AQSI state and explicit legacy-environment migration status."""
    service = IntegrationService(session)
    try:
        integration = get_aqsi_integration(session)
    except AmbiguousIntegrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    legacy_available = settings.aqsi_api_key is not None
    if integration is None:
        return AqsiSettingsRead(
            integration=None,
            status="legacy" if legacy_available else "not_connected",
            using_legacy_environment=legacy_available,
            legacy_available=legacy_available,
        )
    projection = service.read(integration)
    return AqsiSettingsRead(
        integration=projection,
        status=(
            "connected"
            if integration.enabled and projection.credential_saved_at is not None
            else "disabled"
            if not integration.enabled
            else "not_connected"
        ),
        using_legacy_environment=False,
        legacy_available=legacy_available,
    )


@router.post("/aqsi/migrate", response_model=IntegrationRead)
def migrate_legacy_aqsi(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_admin)],
) -> IntegrationRead:
    """Copy the legacy AQSI secret into encrypted Settings-backed storage."""
    if settings.aqsi_api_key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Устаревший API key не найден.")
    try:
        if get_aqsi_integration(session) is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Интеграция AQSI уже создана.")
        service = _service(session, settings, require_cipher=True)
        integration = service.create(
            IntegrationCreate(
                provider=IntegrationProvider.AQSI,
                name="AQSI",
                enabled=settings.aqsi_enabled,
                configuration={
                    "shop_id": settings.aqsi_shop_id,
                    "tax_code": settings.aqsi_tax_code,
                    "device_id": settings.aqsi_sale_spike_device_id,
                    "tax_system_code": settings.aqsi_sale_spike_tax_system_code,
                    "acquiring_mode": settings.aqsi_acquiring_mode,
                    "default_group_id": settings.aqsi_default_group_id,
                    "default_group_name": settings.aqsi_default_group_name,
                },
            ),
            actor_id=current_user.id,
        )
        service.replace_api_key(
            integration.id,
            settings.aqsi_api_key.get_secret_value(),
            actor_id=current_user.id,
        )
    except AmbiguousIntegrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MasterEncryptionKeyError as exc:
        raise _master_key_problem(exc) from exc
    return service.read(integration)


@router.post("/{integration_id}/test", response_model=ConnectionTestRead)
def test_connection(
    integration_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionTestRead:
    """Decrypt server-side and perform a harmless AQSI shops read."""
    try:
        service = _service(session, settings, require_cipher=True)
        integration = service.get(integration_id)
        if integration.provider is not IntegrationProvider.AQSI:
            raise HTTPException(status.HTTP_409_CONFLICT, "Provider test is not implemented.")
        runtime = _aqsi_runtime_settings(integration, service, settings)
        with AqsiHttpClient(runtime) as client:
            client.list_shop_ids()
        return ConnectionTestRead(ok=True, message="Подключение успешно")
    except IntegrationNotFoundError as exc:
        raise _not_found(exc) from exc
    except (MasterEncryptionKeyError, CredentialDecryptionError) as exc:
        raise _master_key_problem(exc) from exc
    except IntegrationConfigurationError:
        return ConnectionTestRead(ok=False, message="API key не настроен")
    except AqsiApiError as exc:
        logger.warning(
            "AQSI connection test failed",
            extra={
                "integration_id": str(integration_id),
                "provider_error_code": _safe_provider_error_code(exc.code),
            },
        )
        message = (
            "API key отклонён AQSI"
            if exc.code in {"http_401", "http_403", "unauthorized", "forbidden"}
            else "Не удалось подключиться к AQSI"
        )
        return ConnectionTestRead(ok=False, message=message)


@router.get("/{integration_id}/shops", response_model=list[AqsiShopRead])
def discover_aqsi_shops(
    integration_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[AqsiShopRead]:
    """List shop IDs supported by the existing AQSI adapter without raw payloads."""
    try:
        service = _service(session, settings, require_cipher=True)
        integration = service.get(integration_id)
        if integration.provider is not IntegrationProvider.AQSI:
            raise HTTPException(status.HTTP_409_CONFLICT, "Provider discovery is not implemented.")
        runtime = _aqsi_runtime_settings(integration, service, settings)
        with AqsiHttpClient(runtime) as client:
            shop_ids = client.list_shop_ids()
    except IntegrationNotFoundError as exc:
        raise _not_found(exc) from exc
    except (MasterEncryptionKeyError, CredentialDecryptionError) as exc:
        raise _master_key_problem(exc) from exc
    except IntegrationConfigurationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "API key не настроен.") from exc
    except AqsiApiError as exc:
        logger.warning(
            "AQSI shop discovery failed",
            extra={
                "integration_id": str(integration_id),
                "provider_error_code": _safe_provider_error_code(exc.code),
            },
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Не удалось получить магазины AQSI.",
        ) from exc
    return [AqsiShopRead(id=shop_id, name=f"Магазин AQSI {shop_id}") for shop_id in shop_ids]


@router.post(
    "/{integration_id}/sync",
    response_model=CatalogSyncRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def synchronize_catalog_now(
    integration_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_admin)],
    queue: Annotated[Queue, Depends(get_default_queue)],
) -> CatalogSyncRequestRead:
    """Queue an attributed full AQSI catalog sweep without waiting on provider I/O."""
    service = IntegrationService(session)
    try:
        integration = service.get(integration_id)
    except IntegrationNotFoundError as exc:
        raise _not_found(exc) from exc
    if integration.provider is not IntegrationProvider.AQSI:
        raise HTTPException(status.HTTP_409_CONFLICT, "Catalog sync is not supported.")
    if not integration.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Интеграция AQSI выключена.")
    if service.read(integration).credential_saved_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "API key не настроен.")
    try:
        queue.enqueue(
            synchronize_aqsi_catalog,
            str(current_user.id),
            True,
            job_timeout=300,
        )
    except RedisError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Синхронизацию не удалось поставить в очередь.",
        ) from exc
    return CatalogSyncRequestRead(
        queued=True,
        message="Проверка каталога поставлена в очередь.",
    )

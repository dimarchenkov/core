from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.catalog.barcodes import BarcodeValidationError
from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.integrations.checkout import (
    CheckoutIntegrationUnavailableError,
    CheckoutProviderFactory,
)
from core.integrations.enums import IntegrationProvider
from core.integrations.models import Integration
from core.inventory.service import InventoryService
from core.rental.enums import AssetPurpose
from core.rental.models import RentalAssetRecord
from core.sales.checkout import (
    CheckoutEmptySaleError,
    CheckoutProviderMismatchError,
    CheckoutService,
    CheckoutStateError,
)
from core.sales.enums import (
    CheckoutPaymentOption,
    FinancialActorType,
    FinancialEntityType,
    FinancialEventType,
    FiscalizationJobStatus,
    FiscalizationStatus,
    FiscalVerificationOutcome,
    IncidentKind,
    IncidentResolutionChoice,
    IncidentResolutionStatus,
    IncidentResolutionType,
    PaymentJobStatus,
    PaymentStatus,
    SaleStatus,
)
from core.sales.financial_events import (
    append_financial_event,
    financial_event_exists,
    list_financial_events,
)
from core.sales.fiscalization_jobs import enqueue_sale_fiscalization
from core.sales.health import collect_checkout_worker_health
from core.sales.incident_resolutions import (
    IncidentResolutionConflictError,
    IncidentResolutionNotFoundError,
    IncidentResolutionService,
)
from core.sales.models import (
    FinancialIncidentResolution,
    Fiscalization,
    FiscalizationJob,
    PaymentAttempt,
    PaymentJob,
    Sale,
)
from core.sales.payment_jobs import enqueue_sale_payment
from core.sales.providers import CheckoutProviderBinding
from core.sales.repository import SaleRepository
from core.sales.schemas import (
    CheckoutContextRead,
    CheckoutHealthRead,
    CheckoutOperationDetailRead,
    CheckoutStartRequest,
    CheckoutStockWarningRead,
    EmergencyFallbackRequest,
    EmergencyIncidentResolutionRead,
    EmergencyIncidentResolutionRequest,
    FiscalVerificationRequest,
    IncidentResolutionRead,
    SaleDiscountUpdate,
    SaleItemAddBarcode,
    SaleItemAddManual,
    SaleItemAddVariant,
    SaleItemQuantityChange,
    SaleRead,
    SingleChargeResolutionRequest,
)
from core.sales.service import (
    SaleBarcodeNotFoundError,
    SaleDiscountInvalidError,
    SaleItemNotFoundError,
    SaleManualItemInvalidError,
    SaleNotDraftError,
    SaleNotFoundError,
    SalePriceMissingError,
    SaleService,
    SaleVariantUnavailableError,
)
from core.shared.db import UUIDv7

router = APIRouter(
    prefix="/api/sales",
    tags=["sales"],
    dependencies=[Depends(get_current_user)],
)


def get_sale_service(session: Annotated[Session, Depends(get_session)]) -> SaleService:
    """Provide Sales application commands for the current request transaction."""
    return SaleService(session)


def _checkout_binding(
    session: Session,
    settings: Settings,
    sale: Sale,
    *,
    for_new: bool = False,
    payment_option: CheckoutPaymentOption | None = None,
) -> CheckoutProviderBinding | None:
    """Resolve a new or persisted Integration without legacy environment fallback."""
    if payment_option is CheckoutPaymentOption.CASH_WITHOUT_RECEIPT:
        return None
    factory = CheckoutProviderFactory(session, settings)
    if for_new or not sale.payments:
        return factory.for_new_checkout()
    if sale.fiscalization is not None:
        if sale.fiscalization.status is FiscalizationStatus.SKIPPED:
            return None
        if sale.fiscalization.integration_id is None:
            raise CheckoutIntegrationUnavailableError("Не найдена касса для фискального чека")
        latest = max(sale.payments, key=lambda item: item.attempt_number)
        return factory.for_recovery(
            payment_integration_id=latest.integration_id,
            fiscal_integration_id=sale.fiscalization.integration_id,
        )
    latest = max(sale.payments, key=lambda item: item.attempt_number)
    if not latest.fiscalization_required:
        return None
    fiscal_integration_id = latest.selected_fiscal_integration_id
    if latest.integration_id is None and fiscal_integration_id is None:
        return factory.for_new_checkout()
    return factory.for_recovery(
        payment_integration_id=latest.integration_id,
        fiscal_integration_id=fiscal_integration_id,
    )


def _sellable_balances(session: Session, variant_ids: list[UUIDv7]) -> dict[UUIDv7, Decimal]:
    """Apply the established sale read-model rule without consuming RentalAsset identity."""
    balances = InventoryService(session).get_balances(variant_ids)
    if not variant_ids:
        return balances
    reserved = dict(
        session.execute(
            select(RentalAssetRecord.variant_id, func.count(RentalAssetRecord.id))
            .where(
                RentalAssetRecord.variant_id.in_(variant_ids),
                RentalAssetRecord.purpose != AssetPurpose.SALE,
                RentalAssetRecord.deleted_at.is_(None),
            )
            .group_by(RentalAssetRecord.variant_id)
        )
        .tuples()
        .all()
    )
    return {
        variant_id: balance - reserved.get(variant_id, 0)
        for variant_id, balance in balances.items()
    }


@router.post("", response_model=SaleRead, status_code=status.HTTP_201_CREATED)
def create_sale(
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Create one empty operator-owned DRAFT Sale."""
    return service.create_sale(owner_id=current_user.id)


@router.get("", response_model=list[SaleRead])
def list_draft_sales(
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sequence[Sale]:
    """List only the current operator's resumable DRAFT Sales."""
    return service.list_draft_sales(owner_id=current_user.id)


@router.get("/operations/history", response_model=list[SaleRead])
def list_checkout_operations(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
    state: str = Query(default="all", pattern="^(all|attention|pending|failed|completed)$"),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> Sequence[Sale]:
    """Return retained payment/fiscal history correlated by Sale."""
    statement = select(Sale).where(
        Sale.owner_id == current_user.id,
        Sale.payments.any(),
        Sale.deleted_at.is_(None),
    )
    if date_from is not None:
        statement = statement.where(Sale.created_at >= date_from)
    if date_to is not None:
        statement = statement.where(Sale.created_at <= date_to)
    if state == "attention":
        checked_at = datetime.now(UTC)
        queued_before = checked_at - timedelta(
            seconds=settings.checkout_queued_job_stale_seconds
        )
        processing_before = checked_at - timedelta(
            seconds=settings.checkout_processing_job_stale_seconds
        )
        statement = statement.where(
            or_(
                Sale.payments.any(PaymentAttempt.status == PaymentStatus.UNKNOWN),
                Sale.fiscalization.has(Fiscalization.status == FiscalizationStatus.UNKNOWN),
                Sale.fiscalization.has(
                    (Fiscalization.status == FiscalizationStatus.FAILED)
                    & func.coalesce(
                        Fiscalization.provider_metadata[
                            "operator_dismissed"
                        ].as_boolean(),
                        False,
                    ).is_(False)
                ),
                Sale.payments.any(
                    PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN})
                    & PaymentAttempt.provider_metadata[
                        "accounting_superseded"
                    ].as_boolean().is_(True)
                ),
                Sale.payments.any(
                    PaymentAttempt.provider_metadata[
                        "late_provider_approval_detected"
                    ].as_boolean().is_(True)
                )
                & ~Sale.incident_resolutions.any(
                    (FinancialIncidentResolution.incident_kind
                    == IncidentKind.PAYMENT_DUPLICATE_RISK)
                    & (FinancialIncidentResolution.resolution_status
                    == IncidentResolutionStatus.RESOLVED)
                    & FinancialIncidentResolution.deleted_at.is_(None)
                ),
                Sale.incident_resolutions.any(
                    (FinancialIncidentResolution.incident_kind
                    == IncidentKind.FISCAL_RECEIPT_VERIFICATION)
                    & (FinancialIncidentResolution.resolution_status
                    != IncidentResolutionStatus.RESOLVED)
                    & FinancialIncidentResolution.deleted_at.is_(None)
                ),
                Sale.fiscalization.has(
                    Fiscalization.provider_metadata[
                        "external_receipt_confirmed"
                    ].as_boolean().is_(True)
                )
                & ~Sale.incident_resolutions.any(
                    (FinancialIncidentResolution.incident_kind
                    == IncidentKind.FISCAL_RECEIPT_VERIFICATION)
                    & FinancialIncidentResolution.deleted_at.is_(None)
                ),
                Sale.payment_jobs.any(
                    ((PaymentJob.status == PaymentJobStatus.QUEUED)
                    & (PaymentJob.created_at < queued_before))
                    | ((PaymentJob.status == PaymentJobStatus.PROCESSING)
                    & (PaymentJob.updated_at < processing_before))
                ),
                Sale.fiscalization_job.has(
                    ((FiscalizationJob.status == FiscalizationJobStatus.QUEUED)
                    & (FiscalizationJob.created_at < queued_before))
                    | ((FiscalizationJob.status == FiscalizationJobStatus.PROCESSING)
                    & (FiscalizationJob.updated_at < processing_before))
                ),
            )
        )
    elif state == "pending":
        statement = statement.where(
            Sale.status.in_({SaleStatus.PAYMENT_PENDING, SaleStatus.FISCALIZATION_PENDING})
        )
    elif state == "failed":
        statement = statement.where(
            Sale.status == SaleStatus.FISCALIZATION_FAILED,
            ~Sale.fiscalization.has(
                Fiscalization.provider_metadata["operator_dismissed"].as_boolean().is_(True)
            ),
        )
    elif state == "completed":
        statement = statement.where(Sale.status == SaleStatus.COMPLETED)
    return session.scalars(
        statement.order_by(Sale.created_at.desc(), Sale.sale_number.desc()).limit(limit)
    ).all()


@router.get("/operations/health", response_model=CheckoutHealthRead)
def checkout_health(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CheckoutHealthRead:
    """Keep provider, worker and operator-owned operation health distinct."""
    checked_at = datetime.now(UTC)
    global_active_payment = session.scalar(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN}),
            func.coalesce(
                PaymentAttempt.provider_metadata["accounting_superseded"].as_boolean(), False
            ).is_(False),
        )
    )
    global_active_fiscal = session.scalar(
        select(func.count(FiscalizationJob.id)).where(
            FiscalizationJob.status.in_(
                {FiscalizationJobStatus.QUEUED, FiscalizationJobStatus.PROCESSING}
            )
        )
    )
    local_active_payment = session.scalar(
        select(func.count(PaymentAttempt.id))
        .join(Sale, Sale.id == PaymentAttempt.sale_id)
        .where(
            Sale.owner_id == current_user.id,
            PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN}),
            func.coalesce(
                PaymentAttempt.provider_metadata["accounting_superseded"].as_boolean(), False
            ).is_(False),
        )
    )
    local_active_fiscal = session.scalar(
        select(func.count(FiscalizationJob.id))
        .join(Sale, Sale.id == FiscalizationJob.sale_id)
        .where(
            Sale.owner_id == current_user.id,
            FiscalizationJob.status.in_(
                {FiscalizationJobStatus.QUEUED, FiscalizationJobStatus.PROCESSING}
            ),
        )
    )
    manual_review = bool(
        session.scalar(
            select(func.count(PaymentAttempt.id)).where(
                PaymentAttempt.sale.has(Sale.owner_id == current_user.id),
                (
                    (PaymentAttempt.status == PaymentStatus.UNKNOWN)
                    | (
                        PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN})
                        & PaymentAttempt.provider_metadata[
                            "accounting_superseded"
                        ].as_boolean().is_(True)
                    )
                    | (
                        PaymentAttempt.provider_metadata[
                            "late_provider_approval_detected"
                        ].as_boolean().is_(True)
                        & ~PaymentAttempt.sale.has(
                            Sale.incident_resolutions.any(
                                (FinancialIncidentResolution.incident_kind
                                == IncidentKind.PAYMENT_DUPLICATE_RISK)
                                & (FinancialIncidentResolution.resolution_status
                                == IncidentResolutionStatus.RESOLVED)
                                & FinancialIncidentResolution.deleted_at.is_(None)
                            )
                        )
                    )
                ),
            )
        )
    ) or bool(
        session.scalar(
            select(func.count(Fiscalization.id)).where(
                Fiscalization.sale.has(Sale.owner_id == current_user.id),
                Fiscalization.status == FiscalizationStatus.UNKNOWN,
            )
        )
    ) or bool(
        session.scalar(
            select(func.count(Sale.id)).where(
                Sale.owner_id == current_user.id,
                Sale.deleted_at.is_(None),
                or_(
                    Sale.incident_resolutions.any(
                        (FinancialIncidentResolution.incident_kind
                        == IncidentKind.FISCAL_RECEIPT_VERIFICATION)
                        & (FinancialIncidentResolution.resolution_status
                        != IncidentResolutionStatus.RESOLVED)
                        & FinancialIncidentResolution.deleted_at.is_(None)
                    ),
                    Sale.fiscalization.has(
                        Fiscalization.provider_metadata[
                            "external_receipt_confirmed"
                        ].as_boolean().is_(True)
                    )
                    & ~Sale.incident_resolutions.any(
                        (FinancialIncidentResolution.incident_kind
                        == IncidentKind.FISCAL_RECEIPT_VERIFICATION)
                        & FinancialIncidentResolution.deleted_at.is_(None)
                    ),
                ),
            )
        )
    )
    failed_fiscalizations = int(
        session.scalar(
            select(func.count(Fiscalization.id)).where(
                Fiscalization.sale.has(Sale.owner_id == current_user.id),
                Fiscalization.status == FiscalizationStatus.FAILED,
                func.coalesce(
                    Fiscalization.provider_metadata["operator_dismissed"].as_boolean(),
                    False,
                ).is_(False),
            )
        )
        or 0
    )
    global_active = bool(global_active_payment or global_active_fiscal)
    local_active = bool(local_active_payment or local_active_fiscal)
    worker = collect_checkout_worker_health(session, settings, now=checked_at)
    worker_attention = not worker.running or worker.stalled_jobs > 0
    common = {
        "active_operation": local_active,
        "local_active_operation": local_active,
        "global_active_operation": global_active,
        "manual_review_required": manual_review,
        "worker_running": worker.running,
        "worker_last_heartbeat": worker.last_heartbeat,
        "queued_job_count": worker.queued_jobs,
        "processing_job_count": worker.processing_jobs,
        "oldest_queued_job_age_seconds": worker.oldest_queued_age_seconds,
        "stalled_job_count": worker.stalled_jobs,
        "last_worker_error": worker.last_error,
        "failed_fiscalization_count": failed_fiscalizations,
    }
    try:
        binding = CheckoutProviderFactory(session, settings).for_health_check()
    except CheckoutIntegrationUnavailableError as exc:
        return CheckoutHealthRead(
            status="degraded",
            api_reachable=None,
            configured=False,
            message=str(exc),
            checked_at=checked_at,
            **common,
        )
    try:
        payment_configured = binding.payment is not None
        fiscal_configured = binding.fiscal is not None
        payment_health = binding.payment.check_health() if binding.payment is not None else None
        fiscal_health = (
            payment_health
            if binding.fiscal is binding.payment
            else binding.fiscal.check_health()
            if binding.fiscal is not None
            else None
        )
        payment_reachable = payment_health.reachable if payment_health is not None else None
        fiscal_reachable = fiscal_health.reachable if fiscal_health is not None else None
        reachable_values = [
            value for value in (payment_reachable, fiscal_reachable) if value is not None
        ]
        api_reachable = (
            all(reachable_values)
            if reachable_values and payment_configured and fiscal_configured
            else False
            if False in reachable_values
            else None
        )
        device_values = [
            value
            for value in (
                payment_health.physical_device_ready if payment_health is not None else None,
                fiscal_health.physical_device_ready if fiscal_health is not None else None,
            )
            if value is not None
        ]
        physical_device_ready = (
            False if False in device_values else True if device_values else None
        )
        configured = payment_configured and fiscal_configured
        overall = (
            "attention"
            if local_active
            or manual_review
            or failed_fiscalizations
            or worker_attention
            else "healthy"
            if configured and api_reachable is True
            else "degraded"
        )
        message = (
            "Есть операция, которая требует ручной проверки."
            if manual_review
            else "Есть оплата с подтверждённым результатом, но чек не сформирован."
            if failed_fiscalizations
            else "Фоновая обработка не отвечает. Новые команды не запускайте."
            if not worker.running
            else "Есть зависшая фоновая операция."
            if worker.stalled_jobs
            else "Есть незавершённая операция. CORE продолжает проверку на сервере."
            if local_active
            else "Не настроен провайдер оплаты."
            if not payment_configured
            else "Не настроен провайдер фискализации."
            if not fiscal_configured
            else "Провайдер оплаты недоступен."
            if payment_reachable is False
            else "Провайдер фискализации недоступен."
            if fiscal_reachable is False
            else "Физическая касса сообщает о недоступности."
            if physical_device_ready is False
            else "Провайдеры доступны; готовность физической кассы API не подтверждает."
            if physical_device_ready is None
            else "Провайдеры и физическая касса доступны."
        )
        return CheckoutHealthRead(
            status=overall,
            api_reachable=api_reachable,
            payment_provider_reachable=payment_reachable,
            fiscal_provider_reachable=fiscal_reachable,
            physical_device_ready=physical_device_ready,
            configured=configured,
            payment_provider_configured=payment_configured,
            fiscal_provider_configured=fiscal_configured,
            message=message,
            checked_at=checked_at,
            integration_name=binding.integration_name,
            payment_integration_name=(
                binding.integration_name if payment_configured else None
            ),
            fiscal_integration_name=(
                binding.resolved_fiscal_integration_name if fiscal_configured else None
            ),
            **common,
        )
    finally:
        if binding.close is not None:
            binding.close()


@router.get("/operations/{sale_id}/detail", response_model=CheckoutOperationDetailRead)
def checkout_operation_detail(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    event_limit: int = Query(default=100, ge=1, le=200),
    event_before: datetime | None = None,
) -> CheckoutOperationDetailRead:
    """Return bounded diagnostics only after enforcing Sale ownership."""
    sale = _execute(lambda: SaleService(session).get_sale(sale_id, owner_id=current_user.id))
    events = list_financial_events(session, sale_id=sale.id, limit=event_limit, before=event_before)
    payment = max(sale.payments, key=lambda item: item.attempt_number) if sale.payments else None
    fallback = next((item for item in reversed(sale.payments) if item.emergency_fallback), None)
    duplicate_payment = next(
        (item for item in reversed(sale.payments) if item.duplicate_payment_risk),
        None,
    )
    emergency_original = next(
        (
            item
            for item in reversed(sale.payments)
            if fallback is not None
            and not item.emergency_fallback
            and item.accounting_superseded
            and (
                fallback.linked_payment_attempt_id is None
                or fallback.linked_payment_attempt_id == str(item.id)
            )
        ),
        None,
    )
    payment_incident = duplicate_payment or emergency_original
    payment_resolution = next(
        (
            item
            for item in reversed(sale.incident_resolutions)
            if item.incident_kind is IncidentKind.PAYMENT_DUPLICATE_RISK
            and (payment_incident is None or item.incident_id == payment_incident.id)
            and item.deleted_at is None
        ),
        None,
    )
    payment_incident_unresolved = bool(
        payment_incident
        and payment_incident.status
        in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN, PaymentStatus.SUCCEEDED}
        and not (
            payment_resolution
            and payment_resolution.resolution_status is IncidentResolutionStatus.RESOLVED
        )
    )
    review_payment = max(
        (
            item
            for item in sale.payments
            if item.status is PaymentStatus.UNKNOWN
            or (
                item.accounting_superseded
                and item.status in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN}
            )
            or item.duplicate_payment_risk
        ),
        key=lambda item: item.attempt_number,
        default=None,
    )
    fiscal = sale.fiscalization
    fiscal_resolution = next(
        (
            item
            for item in reversed(sale.incident_resolutions)
            if item.incident_kind is IncidentKind.FISCAL_RECEIPT_VERIFICATION
            and (fiscal is None or item.incident_id == fiscal.id)
            and item.deleted_at is None
        ),
        None,
    )
    fiscal_verification_state = (
        "verified"
        if fiscal is not None and fiscal.status is FiscalizationStatus.SUCCEEDED
        else "verified"
        if fiscal_resolution
        and fiscal_resolution.resolution_type
        is IncidentResolutionType.FISCAL_RECEIPT_VERIFIED
        else "missing"
        if fiscal_resolution
        and fiscal_resolution.resolution_type
        is IncidentResolutionType.FISCAL_RECEIPT_MISSING
        else "unknown"
        if fiscal_resolution
        and fiscal_resolution.resolution_type
        is IncidentResolutionType.FISCAL_VERIFICATION_UNKNOWN
        else "operator_reported"
        if fiscal is not None and fiscal.external_receipt_confirmed and fallback is not None
        else "unknown"
        if fiscal is not None and fiscal.status is FiscalizationStatus.UNKNOWN
        else "not_required"
    )
    fiscal_action_required = fiscal_verification_state in {
        "operator_reported",
        "missing",
        "unknown",
    }
    manual_review = bool(
        (
            review_payment
            and (not review_payment.duplicate_payment_risk or payment_incident_unresolved)
        )
        or fiscal_action_required
    )
    emergency_eligible = bool(
        fallback is None
        and sale.status is SaleStatus.PAYMENT_PENDING
        and payment is not None
        and payment.payment_method.value == "card"
        and payment.status in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN}
        and fiscal is None
        and (current_user.is_admin or current_user.is_superuser)
    )
    active = sale.status in {SaleStatus.PAYMENT_PENDING, SaleStatus.FISCALIZATION_PENDING}
    recovery_state = (
        "duplicate_payment_risk"
        if payment_incident_unresolved
        else "fiscal_receipt_missing"
        if fiscal_verification_state == "missing"
        else "fiscal_verification_required"
        if fiscal_verification_state == "operator_reported"
        else "fiscal_verification_unknown"
        if fiscal_verification_state == "unknown"
        else "manual_review_required"
        if manual_review
        else "external_pos_completed"
        if fallback
        else "processing"
        if active
        else "completed"
        if sale.status is SaleStatus.COMPLETED
        else "failed"
        if sale.status in {SaleStatus.PAYMENT_FAILED, SaleStatus.FISCALIZATION_FAILED}
        else "idle"
    )
    instruction = (
        "Сверьте операции AQSI и подтвердите одно списание только при наличии доказательства."
        if payment_incident_unresolved
        else (
            "Требуется фискализация: найден только эквайринговый слип, фискальный чек отсутствует."
        )
        if fiscal_verification_state == "missing"
        else "Проверьте наличие фискального чека отдельно от эквайринговой оплаты."
        if fiscal_action_required
        else "Результат исходной операции неизвестен. Проверьте её у провайдера."
        if fallback
        else (
            "Результат операции неизвестен. Не повторяйте списание без проверки "
            "записей провайдера."
        )
        if manual_review
        else None
    )
    integration_id = (
        review_payment.integration_id
        if review_payment is not None and review_payment.integration_id is not None
        else fiscal.integration_id
        if fiscal is not None and fiscal.integration_id is not None
        else payment.integration_id
        if payment
        else None
    )
    integration = session.get(Integration, integration_id) if integration_id else None
    provider_identity = None
    if integration is not None:
        device_id = integration.configuration.get("device_id")
        provider_identity = (
            f"{integration.name} · устройство {device_id}" if device_id else integration.name
        )
    correlation_ids = list(
        dict.fromkeys(
            [
                *(job.correlation_id for job in sale.payment_jobs),
                *([sale.fiscalization_job.correlation_id] if sale.fiscalization_job else []),
            ]
        )
    )
    provider_operation_ids = list(
        dict.fromkeys(
            [
                *(item.external_id for item in sale.payments if item.external_id),
                *([fiscal.external_id] if fiscal and fiscal.external_id else []),
            ]
        )
    )
    return CheckoutOperationDetailRead(
        sale=SaleRead.model_validate(sale),
        events=list(events),
        recovery_state=recovery_state,
        manual_review_required=manual_review,
        manual_review_payment_id=review_payment.id if review_payment else None,
        emergency_fallback_recorded=fallback is not None,
        emergency_fallback_eligible=emergency_eligible,
        duplicate_payment_risk=duplicate_payment is not None,
        payment_incident_unresolved=payment_incident_unresolved,
        can_confirm_single_charge=bool(
            payment_incident_unresolved
            and duplicate_payment is not None
            and duplicate_payment.status is PaymentStatus.SUCCEEDED
            and duplicate_payment.external_id
            and len(
                [
                    item
                    for item in sale.payments
                    if item.payment_method.value == "card"
                    and item.provider is not None
                    and item.status is PaymentStatus.SUCCEEDED
                ]
            )
            == 1
        ),
        payment_resolution=(
            IncidentResolutionRead.model_validate(payment_resolution)
            if payment_resolution
            else None
        ),
        fiscal_verification_state=fiscal_verification_state,
        fiscal_action_required=fiscal_action_required,
        fiscal_resolution=(
            IncidentResolutionRead.model_validate(fiscal_resolution)
            if fiscal_resolution
            else None
        ),
        operator_instruction=instruction,
        provider_identity=provider_identity,
        correlation_ids=correlation_ids,
        provider_operation_ids=provider_operation_ids,
    )


@router.post(
    "/operations/{sale_id}/resolve-single-charge",
    response_model=IncidentResolutionRead,
)
def resolve_single_charge_incident(
    sale_id: UUIDv7,
    data: SingleChargeResolutionRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinancialIncidentResolution:
    """Record an owned operator's evidence-based one-charge conclusion."""
    try:
        return IncidentResolutionService(session).confirm_single_charge(
            sale_id,
            owner_id=current_user.id,
            confirmation=data.confirmation,
            evidence_reference=data.evidence_reference,
            operator_note=data.operator_note,
        )
    except IncidentResolutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except IncidentResolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/operations/{sale_id}/fiscal-verification",
    response_model=IncidentResolutionRead,
)
def record_fiscal_verification(
    sale_id: UUIDv7,
    data: FiscalVerificationRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> FinancialIncidentResolution:
    """Record external fiscal evidence without issuing a receipt or payment command."""
    try:
        return IncidentResolutionService(session).record_fiscal_verification(
            sale_id,
            owner_id=current_user.id,
            outcome=data.outcome,
            confirmation=data.confirmation,
            evidence_reference=data.evidence_reference,
            operator_note=data.operator_note,
            fiscal_fn=data.fiscal_fn,
            fiscal_fd=data.fiscal_fd,
            fiscal_fpd=data.fiscal_fpd,
        )
    except IncidentResolutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except IncidentResolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/operations/{sale_id}/resolve-incident",
    response_model=EmergencyIncidentResolutionRead,
)
def resolve_emergency_incident(
    sale_id: UUIDv7,
    data: EmergencyIncidentResolutionRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> EmergencyIncidentResolutionRead:
    """Record one compact operator choice without rewriting provider facts."""
    if not data.confirmation:
        raise HTTPException(status_code=409, detail="Подтвердите результат проверки")
    try:
        service = IncidentResolutionService(session)
        if data.choice is IncidentResolutionChoice.CORRECTION_RECEIPT_FORMED:
            service.record_fiscal_verification(
                sale_id,
                owner_id=current_user.id,
                outcome=FiscalVerificationOutcome.VERIFIED,
                confirmation=True,
                evidence_reference="Оператор подтвердил формирование чека коррекции",
                operator_note=data.operator_note,
            )
            message = "Формирование чека коррекции записано как операторское подтверждение."
        elif data.choice is IncidentResolutionChoice.EXTERNAL_POS_PAYMENT_CONFIRMED:
            sale = SaleService(session).get_sale(sale_id, owner_id=current_user.id)
            fallback = next(
                (item for item in reversed(sale.payments) if item.emergency_fallback),
                None,
            )
            if fallback is None:
                if not (current_user.is_admin or current_user.is_superuser):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Для аварийного подтверждения требуются права администратора.",
                    )
                sale = CheckoutService(session, None).confirm_external_pos_payment(
                    sale_id,
                    owner_id=current_user.id,
                    risk_acknowledged=True,
                    operator_note=data.operator_note,
                )
                if any(
                    item.accounting_superseded
                    and item.external_id is not None
                    and item.job is not None
                    and item.job.status is PaymentJobStatus.QUEUED
                    for item in sale.payments
                ):
                    enqueue_sale_payment(sale.id)
            service.record_payment_review(
                sale_id,
                owner_id=current_user.id,
                resolution_type=IncidentResolutionType.EXTERNAL_POS_PAYMENT_CONFIRMED,
                confirmation=True,
                operator_note=data.operator_note,
            )
            message = "Оплата на кассе записана; исходная операция остаётся на сверке."
        elif data.choice is IncidentResolutionChoice.ORIGINAL_OPERATION_CANCELED:
            service.record_payment_review(
                sale_id,
                owner_id=current_user.id,
                resolution_type=(
                    IncidentResolutionType.ORIGINAL_OPERATION_CANCELED_CONFIRMED
                ),
                confirmation=True,
                operator_note=data.operator_note,
            )
            message = "Проверка финальной отмены исходной операции записана."
        else:
            sale = SaleService(session).get_sale(sale_id, owner_id=current_user.id)
            if any(item.emergency_fallback for item in sale.payments):
                service.record_payment_review(
                    sale_id,
                    owner_id=current_user.id,
                    resolution_type=IncidentResolutionType.MANUAL_REVIEW_CONFIRMED,
                    confirmation=True,
                    operator_note=data.operator_note,
                )
            else:
                service.record_fiscal_verification(
                    sale_id,
                    owner_id=current_user.id,
                    outcome=FiscalVerificationOutcome.UNKNOWN,
                    confirmation=True,
                    operator_note=(
                        data.operator_note
                        or "Проверено вручную; результат не меняет фискальный факт."
                    ),
                )
            message = "Ручная проверка записана; неизвестные факты остаются открытыми."
        return EmergencyIncidentResolutionRead(choice=data.choice, message=message)
    except SaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except IncidentResolutionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except IncidentResolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/operations/{sale_id}/reconcile", response_model=SaleRead)
def reconcile_checkout_operation(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Requeue read-only provider reconciliation without creating a new charge."""
    sale = SaleRepository(session).get_owned_for_update(sale_id, current_user.id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found.")
    queued = False
    payment_reconciliation = False
    fallback = next((item for item in reversed(sale.payments) if item.emergency_fallback), None)
    fallback_original = max(
        (
            item
            for item in sale.payments
            if item.accounting_superseded
            and not item.emergency_fallback
            and item.status in {PaymentStatus.PENDING, PaymentStatus.UNKNOWN}
        ),
        key=lambda item: item.attempt_number,
        default=None,
    )
    if (sale.status is SaleStatus.PAYMENT_PENDING and sale.payments) or (
        fallback is not None and fallback_original is not None
    ):
        payment = fallback_original or max(
            sale.payments, key=lambda item: item.attempt_number
        )
        payment_reconciliation = True
        job = payment.job
        if job is None:
            raise HTTPException(status_code=409, detail="Задание проверки оплаты отсутствует")
        if payment.external_id is None:
            _record_missing_provider_id_review(
                session,
                sale=sale,
                entity_id=payment.id,
                correlation_id=job.correlation_id,
                current_user=current_user,
                provider=payment.provider,
                previous_status=payment.status.value,
                message="Идентификатор провайдера отсутствует; автоматическая сверка невозможна.",
                instruction="Не повторяйте списание без проверки записей провайдера.",
            )
            session.commit()
            raise HTTPException(
                status_code=409,
                detail="Идентификатор провайдера неизвестен; повторная оплата заблокирована",
            )
        if job.status is PaymentJobStatus.PROCESSING:
            session.commit()
            return sale
        if job.status is PaymentJobStatus.QUEUED:
            if job.reconciliation_requested_at is not None:
                session.commit()
                return sale
            queued = True
            job.next_retry_at = datetime.now(UTC)
            job.reconciliation_requested_at = datetime.now(UTC)
            append_financial_event(
                session,
                sale_id=sale.id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=payment.id,
                event_type=FinancialEventType.RECONCILIATION_REQUESTED,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.OPERATOR,
                actor_id=current_user.id,
                previous_status=PaymentJobStatus.QUEUED.value,
                new_status=PaymentJobStatus.QUEUED.value,
                provider=payment.provider,
                provider_operation_id=payment.external_id,
            )
        else:
            previous_status = job.status
            job.status = PaymentJobStatus.QUEUED
            job.next_retry_at = datetime.now(UTC)
            job.reconciliation_requested_at = datetime.now(UTC)
            job.last_error = None
            append_financial_event(
                session,
                sale_id=sale.id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=payment.id,
                event_type=FinancialEventType.RECONCILIATION_REQUESTED,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.OPERATOR,
                actor_id=current_user.id,
                previous_status=previous_status.value,
                new_status=PaymentJobStatus.QUEUED.value,
                provider=payment.provider,
                provider_operation_id=payment.external_id,
            )
            queued = True
    elif sale.status is SaleStatus.FISCALIZATION_PENDING and sale.fiscalization_job:
        job = sale.fiscalization_job
        fiscal = sale.fiscalization
        if fiscal is None or fiscal.external_id is None:
            correlation_id = job.correlation_id
            _record_missing_provider_id_review(
                session,
                sale=sale,
                entity_id=fiscal.id if fiscal else job.id,
                correlation_id=correlation_id,
                current_user=current_user,
                provider=fiscal.provider if fiscal else None,
                previous_status=fiscal.status.value if fiscal else None,
                message="Идентификатор чека отсутствует; автоматическая сверка невозможна.",
                instruction="Не создавайте новый чек без проверки записей провайдера.",
            )
            session.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    "Идентификатор провайдера неизвестен; повторное создание чека заблокировано"
                ),
            )
        if job.status is FiscalizationJobStatus.PROCESSING:
            session.commit()
            return sale
        if job.status is FiscalizationJobStatus.QUEUED:
            if job.reconciliation_requested_at is not None:
                session.commit()
                return sale
            queued = True
            job.reconciliation_requested_at = datetime.now(UTC)
            append_financial_event(
                session,
                sale_id=sale.id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=fiscal.id,
                event_type=FinancialEventType.RECONCILIATION_REQUESTED,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.OPERATOR,
                actor_id=current_user.id,
                previous_status=FiscalizationJobStatus.QUEUED.value,
                new_status=FiscalizationJobStatus.QUEUED.value,
                provider=fiscal.provider,
                provider_operation_id=fiscal.external_id,
            )
        else:
            previous_status = job.status
            job.status = FiscalizationJobStatus.QUEUED
            job.next_retry_at = datetime.now(UTC)
            job.reconciliation_requested_at = datetime.now(UTC)
            job.last_error = None
            append_financial_event(
                session,
                sale_id=sale.id,
                entity_type=FinancialEntityType.RECONCILIATION,
                entity_id=fiscal.id,
                event_type=FinancialEventType.RECONCILIATION_REQUESTED,
                correlation_id=job.correlation_id,
                actor_type=FinancialActorType.OPERATOR,
                actor_id=current_user.id,
                previous_status=previous_status.value,
                new_status=FiscalizationJobStatus.QUEUED.value,
                provider=fiscal.provider,
                provider_operation_id=fiscal.external_id,
            )
            queued = True
    if not queued:
        raise HTTPException(status_code=409, detail="Операция не требует сверки")
    session.commit()
    if payment_reconciliation:
        enqueue_sale_payment(sale.id)
    else:
        enqueue_sale_fiscalization(sale.id)
    return sale


def _record_missing_provider_id_review(
    session: Session,
    *,
    sale: Sale,
    entity_id: UUIDv7,
    correlation_id: str,
    current_user: User,
    provider: IntegrationProvider | None,
    previous_status: str | None,
    message: str,
    instruction: str,
) -> None:
    """Persist one bounded manual-review marker for a command that cannot be queried."""
    if financial_event_exists(
        session,
        correlation_id=correlation_id,
        event_type=FinancialEventType.MANUAL_REVIEW_REQUIRED,
    ):
        return
    append_financial_event(
        session,
        sale_id=sale.id,
        entity_type=FinancialEntityType.RECONCILIATION,
        entity_id=entity_id,
        event_type=FinancialEventType.RECONCILIATION_INCONCLUSIVE,
        correlation_id=correlation_id,
        actor_type=FinancialActorType.OPERATOR,
        actor_id=current_user.id,
        previous_status=previous_status,
        new_status=previous_status,
        provider=provider,
        error_code="missing_provider_operation_id",
        safe_message=message,
    )
    append_financial_event(
        session,
        sale_id=sale.id,
        entity_type=FinancialEntityType.RECONCILIATION,
        entity_id=entity_id,
        event_type=FinancialEventType.MANUAL_REVIEW_REQUIRED,
        correlation_id=correlation_id,
        actor_type=FinancialActorType.OPERATOR,
        actor_id=current_user.id,
        provider=provider,
        safe_message=instruction,
    )


@router.post("/auto/items/by-variant", response_model=SaleRead, status_code=status.HTTP_201_CREATED)
def add_variant_to_new_sale(
    data: SaleItemAddVariant,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Create a DRAFT Sale and add a Variant when the browser has no active cart."""
    return _execute(
        lambda: service.add_variant_to_new_sale(data.variant_id, owner_id=current_user.id)
    )


@router.post("/auto/items/by-barcode", response_model=SaleRead, status_code=status.HTTP_201_CREATED)
def add_barcode_to_new_sale(
    data: SaleItemAddBarcode,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Create a DRAFT Sale only after the scanned item is valid and priced."""
    return _execute(lambda: service.add_barcode_to_new_sale(data.barcode, owner_id=current_user.id))


@router.get("/{sale_id}", response_model=SaleRead)
def get_sale(
    sale_id: UUIDv7,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Return one owned Sale, including cancelled history."""
    return _execute(lambda: service.get_sale(sale_id, owner_id=current_user.id))


@router.get("/{sale_id}/checkout/context", response_model=CheckoutContextRead)
def checkout_context(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> CheckoutContextRead:
    """Return payment device and non-blocking stock warnings for confirmation UI."""
    sale = _execute(lambda: SaleService(session).get_sale(sale_id, owner_id=current_user.id))
    catalog_items = [item for item in sale.items if item.variant_id is not None]
    balances = _sellable_balances(session, [item.variant_id for item in catalog_items])
    warnings = [
        CheckoutStockWarningRead(
            variant_id=item.variant_id,
            label=item.display_label_snapshot,
            on_hand=balances[item.variant_id],
            after_sale=balances[item.variant_id] - item.quantity,
        )
        for item in catalog_items
        if balances[item.variant_id] - item.quantity < 0
    ]
    try:
        binding = _checkout_binding(
            session, settings, sale, for_new=sale.status is SaleStatus.DRAFT
        )
    except CheckoutIntegrationUnavailableError as exc:
        return CheckoutContextRead(
            available=True,
            message=f"{exc}. Доступна оплата наличными без чека.",
            fiscalization_available=False,
            stock_warnings=warnings,
        )
    try:
        return CheckoutContextRead(
            available=True,
            message="Выберите оплату наличными или картой / QR",
            integration_id=binding.integration_id,
            integration_name=binding.integration_name,
            provider_name=binding.provider_display_name,
            payment_integration_id=(
                binding.integration_id if binding.payment is not None else None
            ),
            payment_integration_name=(
                binding.integration_name if binding.payment is not None else None
            ),
            payment_provider_name=(
                binding.provider_display_name if binding.payment is not None else None
            ),
            fiscal_integration_id=binding.resolved_fiscal_integration_id,
            fiscal_integration_name=binding.resolved_fiscal_integration_name,
            fiscal_provider_name=binding.resolved_fiscal_provider_display_name,
            acquiring_label=binding.payment_display_name,
            fiscalization_available=True,
            stock_warnings=warnings,
        )
    finally:
        if binding is not None and binding.close is not None:
            binding.close()


@router.post("/{sale_id}/checkout", response_model=SaleRead)
def start_checkout(
    sale_id: UUIDv7,
    data: CheckoutStartRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Freeze and confirm cash or initiate one idempotently guarded acquiring payment."""
    return _run_checkout(
        session,
        settings,
        sale_id,
        current_user.id,
        "start",
        payment_option=data.payment_option,
    )


@router.post("/{sale_id}/checkout/progress", response_model=SaleRead)
def progress_checkout(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Poll persisted provider operations and resume payment/fiscal recovery."""
    return _run_checkout(session, settings, sale_id, current_user.id, "progress")


@router.post("/{sale_id}/checkout/retry-payment", response_model=SaleRead)
def retry_payment(
    sale_id: UUIDv7,
    data: CheckoutStartRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Create a fresh acquiring attempt after a definite provider failure."""
    return _run_checkout(
        session,
        settings,
        sale_id,
        current_user.id,
        "retry_payment",
        payment_option=data.payment_option,
    )


@router.post("/{sale_id}/checkout/retry-fiscalization", response_model=SaleRead)
def retry_fiscalization(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Retry only a definitely failed fiscal operation; never repeat payment."""
    return _run_checkout(session, settings, sale_id, current_user.id, "retry_fiscalization")


@router.post("/{sale_id}/checkout/external-pos", response_model=SaleRead)
def confirm_external_pos_payment(
    sale_id: UUIDv7,
    data: EmergencyFallbackRequest,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Record an administrator-confirmed payment and receipt on the physical POS."""
    if not (current_user.is_admin or current_user.is_superuser):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Для аварийного подтверждения требуются права администратора.",
        )
    try:
        sale = CheckoutService(session, None).confirm_external_pos_payment(
            sale_id,
            owner_id=current_user.id,
            risk_acknowledged=data.risk_acknowledged,
            operator_note=data.operator_note,
        )
        if any(
            item.accounting_superseded
            and item.external_id is not None
            and item.job is not None
            and item.job.status is PaymentJobStatus.QUEUED
            for item in sale.payments
        ):
            enqueue_sale_payment(sale.id)
        return sale
    except SaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except CheckoutStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _run_checkout(
    session: Session,
    settings: Settings,
    sale_id: UUIDv7,
    owner_id: UUIDv7,
    command: str,
    *,
    payment_option: CheckoutPaymentOption = CheckoutPaymentOption.CARD,
) -> Sale:
    """Resolve one Integration and translate checkout failures into stable API errors."""
    try:
        sale = SaleService(session).get_sale(sale_id, owner_id=owner_id)
        binding = _checkout_binding(
            session,
            settings,
            sale,
            for_new=command in {"start", "retry_payment"} and sale.status is SaleStatus.DRAFT,
            payment_option=(payment_option if command in {"start", "retry_payment"} else None),
        )
    except SaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except CheckoutIntegrationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        checkout = CheckoutService(session, binding)
        if command == "start":
            result = checkout.start(
                sale_id,
                owner_id=owner_id,
                payment_option=payment_option,
            )
        elif command == "progress":
            result = checkout.progress(sale_id, owner_id=owner_id)
        elif command == "retry_payment":
            result = checkout.retry_payment(
                sale_id,
                owner_id=owner_id,
                payment_option=payment_option,
            )
        elif command == "retry_fiscalization":
            result = checkout.retry_fiscalization(sale_id, owner_id=owner_id)
        else:
            raise RuntimeError(f"Unknown checkout command: {command}")
        if result.status is SaleStatus.FISCALIZATION_PENDING:
            enqueue_sale_fiscalization(result.id)
        if result.status is SaleStatus.PAYMENT_PENDING:
            enqueue_sale_payment(result.id)
        return result
    except SaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except CheckoutEmptySaleError as exc:
        raise HTTPException(status_code=409, detail="Нельзя оплатить пустую продажу") from exc
    except CheckoutProviderMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail="Продажа связана с другой настройкой кассы",
        ) from exc
    except CheckoutStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        if binding is not None and binding.close is not None:
            binding.close()


@router.post("/{sale_id}/items/by-variant", response_model=SaleRead)
def add_variant(
    sale_id: UUIDv7,
    data: SaleItemAddVariant,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Add or increment a Variant in an owned DRAFT Sale."""
    return _execute(lambda: service.add_variant(sale_id, data.variant_id, owner_id=current_user.id))


@router.post("/{sale_id}/items/by-barcode", response_model=SaleRead)
def add_barcode(
    sale_id: UUIDv7,
    data: SaleItemAddBarcode,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Resolve a canonical barcode and add or increment its Variant."""
    return _execute(lambda: service.add_barcode(sale_id, data.barcode, owner_id=current_user.id))


@router.post("/{sale_id}/items/manual", response_model=SaleRead)
def add_manual_item(
    sale_id: UUIDv7,
    data: SaleItemAddManual,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Add one manual/open item without creating a Catalog or Inventory identity."""
    return _execute(
        lambda: service.add_manual_item(
            sale_id,
            data.name,
            data.unit_price,
            data.quantity,
            owner_id=current_user.id,
        )
    )


@router.patch("/{sale_id}/discount", response_model=SaleRead)
def update_discount(
    sale_id: UUIDv7,
    data: SaleDiscountUpdate,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Set the authoritative percentage discount on one owned DRAFT Sale."""
    return _execute(
        lambda: service.set_discount(sale_id, data.discount_value, owner_id=current_user.id)
    )


@router.post("/{sale_id}/items/{item_id}/quantity", response_model=SaleRead)
def change_quantity(
    sale_id: UUIDv7,
    item_id: UUIDv7,
    data: SaleItemQuantityChange,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Increment or decrement one line using a server-authoritative relative command."""
    return _execute(
        lambda: service.change_quantity(
            sale_id,
            item_id,
            data.delta,
            owner_id=current_user.id,
        )
    )


@router.delete("/{sale_id}/items/{item_id}", response_model=SaleRead)
def remove_item(
    sale_id: UUIDv7,
    item_id: UUIDv7,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Remove one line and return the recalculated Sale."""
    return _execute(lambda: service.remove_item(sale_id, item_id, owner_id=current_user.id))


@router.post("/{sale_id}/cancel", response_model=SaleRead)
def cancel_sale(
    sale_id: UUIDv7,
    service: Annotated[SaleService, Depends(get_sale_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Cancel an owned DRAFT Sale without deleting its history."""
    return _execute(lambda: service.cancel_sale(sale_id, owner_id=current_user.id))


def _execute(command: Callable[[], Sale]) -> Sale:
    """Translate Sales application failures into stable operator-facing API errors."""
    try:
        return command()
    except SaleNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Продажа не найдена") from exc
    except SaleItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Позиция продажи не найдена") from exc
    except SaleNotDraftError as exc:
        raise HTTPException(
            status_code=409, detail="Продажа недоступна для редактирования"
        ) from exc
    except SaleVariantUnavailableError as exc:
        raise HTTPException(status_code=409, detail="Товар недоступен для продажи") from exc
    except SalePriceMissingError as exc:
        raise HTTPException(status_code=409, detail="У товара не указана цена") from exc
    except SaleDiscountInvalidError as exc:
        raise HTTPException(
            status_code=422,
            detail="Скидка не должна обнулять фискальную цену позиции или итог продажи",
        ) from exc
    except SaleManualItemInvalidError as exc:
        raise HTTPException(
            status_code=422, detail="Укажите название, положительную цену и количество"
        ) from exc
    except SaleBarcodeNotFoundError as exc:
        barcode = str(exc.args[0]) if exc.args else ""
        raise HTTPException(
            status_code=404,
            detail=f"Товар со штрихкодом {barcode} не найден",
        ) from exc
    except BarcodeValidationError as exc:
        raise HTTPException(status_code=422, detail="Некорректный штрихкод") from exc

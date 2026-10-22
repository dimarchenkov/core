from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
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
    FiscalizationJobStatus,
    FiscalizationStatus,
    PaymentJobStatus,
    PaymentStatus,
    SaleStatus,
)
from core.sales.fiscalization_jobs import enqueue_sale_fiscalization
from core.sales.models import Fiscalization, FiscalizationJob, PaymentAttempt, Sale
from core.sales.payment_jobs import enqueue_sale_payment
from core.sales.providers import CheckoutProviderBinding
from core.sales.schemas import (
    CheckoutContextRead,
    CheckoutHealthRead,
    CheckoutStartRequest,
    CheckoutStockWarningRead,
    SaleDiscountUpdate,
    SaleItemAddBarcode,
    SaleItemAddManual,
    SaleItemAddVariant,
    SaleItemQuantityChange,
    SaleRead,
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
        return factory.for_recovery(sale.fiscalization.integration_id)
    latest = max(sale.payments, key=lambda item: item.attempt_number)
    if not latest.fiscalization_required:
        return None
    if latest.integration_id is None:
        return factory.for_new_checkout()
    return factory.for_recovery(latest.integration_id)


def _sellable_balances(
    session: Session, variant_ids: list[UUIDv7]
) -> dict[UUIDv7, Decimal]:
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
        ).tuples().all()
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
        statement = statement.where(
            Sale.payments.any(
                PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN})
                & PaymentAttempt.provider_metadata[
                    "accounting_superseded"
                ].as_boolean().is_(True)
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
    """Combine AQSI Cloud reachability and unresolved-operation signals."""
    del current_user
    checked_at = datetime.now(UTC)
    active_payment = session.scalar(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN}),
            func.coalesce(
                PaymentAttempt.provider_metadata["accounting_superseded"].as_boolean(), False
            ).is_(False),
        )
    )
    active_fiscal = session.scalar(
        select(func.count(FiscalizationJob.id)).where(
            FiscalizationJob.status.in_(
                {FiscalizationJobStatus.QUEUED, FiscalizationJobStatus.PROCESSING}
            )
        )
    )
    manual_review = bool(session.scalar(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.status.in_({PaymentStatus.PENDING, PaymentStatus.UNKNOWN}),
            PaymentAttempt.provider_metadata["accounting_superseded"].as_boolean().is_(True),
        )
    ))
    active = bool(active_payment or active_fiscal)
    try:
        binding = CheckoutProviderFactory(session, settings).for_new_checkout()
    except CheckoutIntegrationUnavailableError as exc:
        return CheckoutHealthRead(
            status="degraded",
            api_reachable=None,
            configured=False,
            active_operation=active,
            manual_review_required=manual_review,
            message=str(exc),
            checked_at=checked_at,
        )
    try:
        health = binding.payment.check_health()
        overall = "attention" if active or manual_review else (
            "healthy" if health.reachable else "degraded"
        )
        message = (
            "Продажи сверены. Старая операция AQSI требует проверки."
            if manual_review else
            "Есть незавершённая операция. CORE продолжает проверку на сервере."
            if active
            else health.message
        )
        return CheckoutHealthRead(
            status=overall,
            api_reachable=health.reachable,
            configured=True,
            active_operation=active,
            manual_review_required=manual_review,
            message=message,
            checked_at=datetime.fromisoformat(health.checked_at),
            integration_name=binding.integration_name,
        )
    finally:
        if binding.close is not None:
            binding.close()


@router.post("/operations/{sale_id}/reconcile", response_model=SaleRead)
def reconcile_checkout_operation(
    sale_id: UUIDv7,
    session: Annotated[Session, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Sale:
    """Requeue read-only provider reconciliation without creating a new charge."""
    sale = _execute(
        lambda: SaleService(session).get_sale(sale_id, owner_id=current_user.id)
    )
    queued = False
    if sale.status is SaleStatus.PAYMENT_PENDING and sale.payments:
        payment = max(sale.payments, key=lambda item: item.attempt_number)
        job = payment.job
        if job is None:
            raise HTTPException(status_code=409, detail="Задание проверки оплаты отсутствует")
        if payment.external_id is None and payment.status is PaymentStatus.UNKNOWN:
            raise HTTPException(
                status_code=409,
                detail="Идентификатор AQSI неизвестен; повторная оплата заблокирована",
            )
        job.status = PaymentJobStatus.QUEUED
        job.next_retry_at = datetime.now(UTC)
        job.last_error = None
        queued = True
    elif sale.status is SaleStatus.FISCALIZATION_PENDING and sale.fiscalization_job:
        sale.fiscalization_job.status = FiscalizationJobStatus.QUEUED
        sale.fiscalization_job.next_retry_at = datetime.now(UTC)
        sale.fiscalization_job.last_error = None
        queued = True
    if not queued:
        raise HTTPException(status_code=409, detail="Операция не требует сверки")
    session.commit()
    if sale.status is SaleStatus.PAYMENT_PENDING:
        enqueue_sale_payment(sale.id)
    else:
        enqueue_sale_fiscalization(sale.id)
    return sale


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
            for_new=command in {"start", "retry_payment"}
            and sale.status is SaleStatus.DRAFT,
            payment_option=(
                payment_option if command in {"start", "retry_payment"} else None
            ),
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
        lambda: service.set_discount(
            sale_id, data.discount_value, owner_id=current_user.id
        )
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

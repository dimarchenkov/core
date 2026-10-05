from __future__ import annotations

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from threading import Lock
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.catalog.models import CatalogProduct, CatalogVariant
from core.config import Settings, get_settings
from core.database import get_session
from core.identity.dependencies import get_current_user
from core.identity.models import User
from core.integrations.aqsi.client import (
    AqsiApiError,
    AqsiHttpClient,
    AqsiPendingOrderGateway,
)
from core.pricing.enums import PriceType
from core.pricing.repository import PriceRepository
from core.shared.db import UUIDv7

MONEY = Decimal("0.01")
logger = logging.getLogger(__name__)
_submitted_references: set[str] = set()
_direct_attempts: dict[str, DirectAttempt] = {}
_submission_lock = Lock()

router = APIRouter(prefix="/api/dev/aqsi-sale-spike", tags=["dev", "aqsi-spike"])
page_router = APIRouter(include_in_schema=False)


@page_router.get("/dev/aqsi-sale-spike", response_class=FileResponse)
def aqsi_sale_spike_page() -> FileResponse:
    """Serve the temporary page while keeping every data operation authenticated."""
    from core.web.routes import static_root

    return FileResponse(static_root / "aqsi-sale-spike.html", media_type="text/html")


class SpikeLineRequest(BaseModel):
    """One existing Core Variant with an isolated experimental price."""

    model_config = ConfigDict(extra="forbid")

    variant_id: UUIDv7
    quantity: int = Field(ge=1, le=100)
    test_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    @field_validator("test_price")
    @classmethod
    def quantize_price(cls, value: Decimal) -> Decimal:
        """Keep exact kopeck arithmetic throughout Core."""
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)


class SpikeOrderRequest(BaseModel):
    """Explicitly confirmed request for one real AQSI deferred order."""

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    confirm_real_operation: Literal[True]
    lines: list[SpikeLineRequest] = Field(min_length=1, max_length=3)


class SpikeLineRead(BaseModel):
    """Resolved Core facts and exact experimental line amount."""

    variant_id: UUIDv7
    product_name: str
    variant_name: str
    sku: str
    barcode: str
    core_price: Decimal | None
    test_price: Decimal
    quantity: int
    line_total: Decimal


class SpikeOrderRead(BaseModel):
    """Traceable result returned after AQSI accepts the HTTP request."""

    core_reference: str
    aqsi_reference: str | None
    aqsi_status: str
    device_id: str
    expected_total: Decimal
    lines: list[SpikeLineRead]


DirectState = Literal[
    "payment_in_progress",
    "payment_failed",
    "payment_success_fiscalization_pending",
    "payment_success_fiscalization_failed",
    "payment_success_fiscalization_unknown",
    "completed",
    "unknown",
]


class DirectAttemptRead(BaseModel):
    """Safe operator-facing projection of one process-local checkout attempt."""

    core_reference: str
    state: DirectState
    message: str
    device_id: int
    expected_total: Decimal
    purchase_operation_id: str | None = None
    purchase_status: str | None = None
    slip_reference: str | None = None
    receipt_operation_id: str | None = None
    receipt_status: str | None = None
    aqsi_problem: str | None = None
    lines: list[SpikeLineRead]


@dataclass
class DirectAttempt:
    """Ephemeral state for the deliberately non-domain direct checkout experiment."""

    reference: str
    device_id: int
    total_kopecks: int
    tax_system_code: int
    tax_code: int
    ttl_millis: int
    lines: list[SpikeLineRead]
    state: DirectState = "payment_in_progress"
    message: str = "Операция оплаты создана. Ожидаем AQSI."
    purchase_submission_started: bool = False
    purchase_operation_id: str | None = None
    purchase_status: str | None = None
    slip: dict[str, object] | None = None
    slip_reference: str | None = None
    receipt_submission_started: bool = False
    receipt_operation_id: str | None = None
    receipt_status: str | None = None
    aqsi_problem: str | None = None
    progressing: bool = False


def get_spike_gateway(
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
) -> Generator[AqsiPendingOrderGateway]:
    """Create the real AQSI adapter only for an explicitly invoked spike request."""
    _require_spike_admin(user, settings)
    with AqsiHttpClient(settings) as gateway:
        yield gateway


def _require_spike_admin(user: User, settings: Settings) -> None:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required.")
    if not settings.aqsi_sale_spike_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "AQSI sale spike is disabled.")
    if not settings.aqsi_sale_spike_device_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AQSI spike device is not configured.",
        )


def _display_name(product: str, variant: str) -> str:
    meaningful = variant.strip()
    if meaningful.casefold() in {"default", "default variant", "основной"}:
        meaningful = ""
    return (f"{product}, {meaningful}" if meaningful else product)[:128]


def _resolve_lines(
    session: Session, requested: list[SpikeLineRequest]
) -> list[SpikeLineRead]:
    if len({line.variant_id for line in requested}) != len(requested):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Duplicate Variant selected.")
    prices = PriceRepository(session)
    resolved: list[SpikeLineRead] = []
    for line in requested:
        row = session.execute(
            select(CatalogVariant, CatalogProduct)
            .join(CatalogProduct, CatalogProduct.id == CatalogVariant.product_id)
            .where(
                CatalogVariant.id == line.variant_id,
                CatalogVariant.deleted_at.is_(None),
                CatalogVariant.is_active.is_(True),
                CatalogProduct.deleted_at.is_(None),
                CatalogProduct.is_active.is_(True),
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Active Variant not found.")
        variant, product = row
        current = prices.get_current(variant.id, PriceType.RETAIL, at=datetime.now(UTC))
        resolved.append(
            SpikeLineRead(
                variant_id=variant.id,
                product_name=product.title,
                variant_name=variant.title,
                sku=variant.sku,
                barcode=variant.barcode,
                core_price=current.amount if current else None,
                test_price=line.test_price,
                quantity=line.quantity,
                line_total=(line.test_price * line.quantity).quantize(MONEY),
            )
        )
    return resolved


def _payload(
    reference: str, device_id: str, tax_code: int, lines: list[SpikeLineRead]
) -> dict[str, object]:
    """Map exact Core facts to AQSI's official V2 deferred-order schema."""
    return {
        "id": reference,
        "number": reference[:32],
        "dateTime": datetime.now(UTC).isoformat(),
        "device": device_id,
        "status": "Отложен",
        "comment": "TEMPORARY Core fiscal spike",
        "content": {
            "discountMoney": 0,
            "discountPercent": 0,
            "type": 1,
            "positions": [
                {
                    "positionId": str(uuid4()),
                    "quantity": line.quantity,
                    "price": float(line.test_price),
                    "tax": tax_code,
                    "text": _display_name(line.product_name, line.variant_name),
                    "paymentMethodType": 4,
                    "paymentSubjectType": 1,
                    "unit": "Штука",
                    "unitCode": 0,
                    "sku": line.sku,
                    "barcodes": [line.barcode],
                    "editable": False,
                    "discountMoney": 0,
                    "discountPercent": 0,
                }
                for line in lines
            ],
        },
        "isEditableByDevice": False,
        "ignoreItemCodeCheck": False,
    }


def _direct_configuration(settings: Settings) -> tuple[int, int]:
    """Require explicit fiscal settings before any real acquiring can start."""
    try:
        device_id = int(settings.aqsi_sale_spike_device_id or "")
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AQSI spike device must be a numeric v4 device ID.",
        ) from exc
    tax_system_code = settings.aqsi_sale_spike_tax_system_code
    if tax_system_code not in {1, 2, 4, 16, 32}:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "AQSI tax system is not explicitly configured for direct checkout.",
        )
    return device_id, tax_system_code


def _money_to_kopecks(value: Decimal) -> int:
    return int((value.quantize(MONEY) * 100).to_integral_exact())


def _direct_receipt_payload(attempt: DirectAttempt) -> dict[str, object]:
    """Build the documented v4 receipt from the immutable acquiring basket snapshot."""
    if attempt.slip is None:
        raise ValueError("Completed acquiring slip is required for fiscalization.")
    positions: list[dict[str, object]] = []
    receipt_total = 0
    for line in attempt.lines:
        price_kopecks = _money_to_kopecks(line.test_price)
        receipt_total += price_kopecks * line.quantity
        positions.append(
            {
                "externalId": str(line.variant_id),
                "info": {
                    "calculationTypeId": 4,
                    "calculationSubjectId": 1,
                    "name": _display_name(line.product_name, line.variant_name),
                    "taxRateId": attempt.tax_code,
                    "quantityUnitText": "Штука",
                    "quantityUnitId": 0,
                    "baseQuantity": str(line.quantity),
                    "finalPrice": price_kopecks,
                },
                "barcodes": [line.barcode],
            }
        )
    if receipt_total != attempt.total_kopecks:
        raise ValueError("Receipt basket no longer matches the acquiring amount.")
    return {
        "deviceId": attempt.device_id,
        "ttlMillis": attempt.ttl_millis,
        "typeId": 1,
        "info": {"taxSystemCode": attempt.tax_system_code},
        "positions": positions,
        "payments": [
            {"type": 1, "amount": attempt.total_kopecks, "slip": attempt.slip}
        ],
        "ignoreItemCodeCheck": False,
        "skipPrinting": False,
    }


def _attempt_read(attempt: DirectAttempt) -> DirectAttemptRead:
    return DirectAttemptRead(
        core_reference=attempt.reference,
        state=attempt.state,
        message=attempt.message,
        device_id=attempt.device_id,
        expected_total=(Decimal(attempt.total_kopecks) / 100).quantize(MONEY),
        purchase_operation_id=attempt.purchase_operation_id,
        purchase_status=attempt.purchase_status,
        slip_reference=attempt.slip_reference,
        receipt_operation_id=attempt.receipt_operation_id,
        receipt_status=attempt.receipt_status,
        aqsi_problem=attempt.aqsi_problem,
        lines=attempt.lines,
    )


def _operation_problem(operation: dict[str, object]) -> str | None:
    values = [operation.get("message"), operation.get("problems")]
    message = " · ".join(str(value) for value in values if value)
    return message[:1000] or None


def _completed_slip(
    operation: dict[str, object], expected_amount: int
) -> dict[str, object]:
    raw = operation.get("result")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("AQSI returned an unreadable acquiring result.") from exc
    else:
        parsed = raw
    if not isinstance(parsed, dict):
        raise ValueError("AQSI did not return the completed acquiring slip.")
    slip = cast(dict[str, object], parsed)
    content = slip.get("content")
    if not isinstance(slip.get("id"), str) or not isinstance(content, dict):
        raise ValueError("AQSI returned an incomplete acquiring slip.")
    if content.get("type") != "purchase" or content.get("amount") != expected_amount:
        raise ValueError("AQSI acquiring result does not match the requested payment.")
    return slip


def _advance_direct_attempt(
    attempt: DirectAttempt, gateway: AqsiPendingOrderGateway
) -> None:
    """Advance only along documented final AQSI states, without automatic retries."""
    if attempt.purchase_operation_id is None:
        return
    if attempt.receipt_operation_id is not None:
        operation = gateway.get_operation(attempt.receipt_operation_id)
        receipt_status = operation.get("status")
        attempt.receipt_status = str(receipt_status) if receipt_status else None
        attempt.aqsi_problem = _operation_problem(operation)
        if receipt_status == "Completed":
            attempt.state = "completed"
            attempt.message = "Оплата и фискальный чек успешно завершены."
        elif receipt_status in {"Canceled", "Timeout", "Error"}:
            attempt.state = "payment_success_fiscalization_failed"
            attempt.message = "ОПЛАТА ПРОШЛА, ЧЕК НЕ СФОРМИРОВАН. Не повторяйте оплату."
        elif receipt_status in {"Pending", "Processing", "Finishing"}:
            attempt.state = "payment_success_fiscalization_pending"
            attempt.message = "Оплата прошла. AQSI формирует фискальный чек."
        else:
            attempt.state = "payment_success_fiscalization_unknown"
            attempt.message = "Оплата прошла, статус фискализации неизвестен. Не повторяйте оплату."
        return

    operation = gateway.get_operation(attempt.purchase_operation_id)
    purchase_status = operation.get("status")
    attempt.purchase_status = str(purchase_status) if purchase_status else None
    attempt.aqsi_problem = _operation_problem(operation)
    if purchase_status in {"Canceled", "Timeout", "Error"}:
        attempt.state = "payment_failed"
        attempt.message = "Оплата не выполнена. Новая попытка требует отдельного подтверждения."
        logger.warning(
            "AQSI direct purchase final failure reference=%s operation=%s status=%s",
            attempt.reference,
            attempt.purchase_operation_id,
            purchase_status,
        )
        return
    if purchase_status in {"Pending", "Processing", "Finishing"}:
        attempt.state = "payment_in_progress"
        attempt.message = {
            "Pending": "Команда оплаты находится в очереди AQSI.",
            "Processing": "AQSI выполняет оплату. Следуйте подсказкам на кассе.",
            "Finishing": "AQSI завершает обработку результата оплаты.",
        }[str(purchase_status)]
        return
    if purchase_status != "Completed":
        attempt.state = "unknown"
        attempt.message = "Статус оплаты неизвестен. Не повторяйте оплату; проверьте AQSI."
        return

    try:
        slip = _completed_slip(operation, attempt.total_kopecks)
    except ValueError as exc:
        attempt.state = "payment_success_fiscalization_failed"
        attempt.message = "ОПЛАТА ПРОШЛА, ЧЕК НЕ СФОРМИРОВАН. Не повторяйте оплату."
        attempt.aqsi_problem = str(exc)
        return
    attempt.slip = slip
    attempt.slip_reference = str(slip["id"])
    if attempt.receipt_submission_started:
        attempt.state = "payment_success_fiscalization_unknown"
        attempt.message = "Оплата прошла, статус фискализации неизвестен. Не повторяйте оплату."
        return
    logger.warning(
        "AQSI direct purchase completed reference=%s operation=%s slip=%s amount=%s",
        attempt.reference,
        attempt.purchase_operation_id,
        attempt.slip_reference,
        attempt.total_kopecks,
    )
    attempt.receipt_submission_started = True
    try:
        created = gateway.create_receipt(_direct_receipt_payload(attempt))
    except AqsiApiError as exc:
        attempt.aqsi_problem = f"{exc.code}: {exc}"
        attempt.state = (
            "payment_success_fiscalization_unknown"
            if exc.outcome_unknown
            else "payment_success_fiscalization_failed"
        )
        attempt.message = (
            "ОПЛАТА ПРОШЛА, СТАТУС ЧЕКА НЕИЗВЕСТЕН. Не повторяйте оплату."
            if exc.outcome_unknown
            else "ОПЛАТА ПРОШЛА, ЧЕК НЕ СФОРМИРОВАН. Не повторяйте оплату."
        )
        return
    operation_id = created.get("operationId")
    if not isinstance(operation_id, str) or not operation_id:
        attempt.state = "payment_success_fiscalization_unknown"
        attempt.message = "ОПЛАТА ПРОШЛА, СТАТУС ЧЕКА НЕИЗВЕСТЕН. Не повторяйте оплату."
        attempt.aqsi_problem = "AQSI did not return a receipt operationId."
        return
    attempt.receipt_operation_id = operation_id
    attempt.receipt_status = "Pending"
    attempt.state = "payment_success_fiscalization_pending"
    attempt.message = "Оплата прошла. Фискальный чек отправлен на AQSI."
    logger.warning(
        "AQSI direct receipt submitted reference=%s operation=%s",
        attempt.reference,
        operation_id,
    )


@router.post("/orders", response_model=SpikeOrderRead, status_code=status.HTTP_201_CREATED)
def create_spike_order(
    data: SpikeOrderRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
    gateway: Annotated[AqsiPendingOrderGateway, Depends(get_spike_gateway)],
) -> SpikeOrderRead:
    """Create one real deferred AQSI order without changing any Core business facts."""
    _require_spike_admin(user, settings)
    reference = f"CORE-SPIKE-{data.request_id.hex[:20]}"
    with _submission_lock:
        if reference in _submitted_references:
            raise HTTPException(status.HTTP_409_CONFLICT, "This spike request was already sent.")
        _submitted_references.add(reference)
    request_started = False
    try:
        lines = _resolve_lines(session, data.lines)
        payload = _payload(
            reference,
            settings.aqsi_sale_spike_device_id or "",
            settings.aqsi_tax_code,
            lines,
        )
        logger.warning("Submitting real AQSI spike order reference=%s", reference)
        request_started = True
        result = gateway.create_pending_order(payload)
    except AqsiApiError as exc:
        logger.warning(
            "AQSI spike order failed or has unknown outcome reference=%s code=%s",
            reference,
            exc.code,
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {
                "code": exc.code,
                "message": str(exc),
                "retryable": False,
                "core_reference": reference,
                "outcome_unknown": exc.outcome_unknown,
            },
        ) from exc
    except Exception:
        if not request_started:
            with _submission_lock:
                _submitted_references.discard(reference)
        raise
    return SpikeOrderRead(
        core_reference=reference,
        aqsi_reference=str(result.get("guid")) if result.get("guid") else None,
        aqsi_status="Запрос принят API; состояние заказа проверьте отдельно",
        device_id=settings.aqsi_sale_spike_device_id or "",
        expected_total=sum((line.line_total for line in lines), Decimal("0.00")),
        lines=lines,
    )


@router.get("/orders/{reference}")
def get_spike_order_status(
    reference: str,
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
    gateway: Annotated[AqsiPendingOrderGateway, Depends(get_spike_gateway)],
) -> dict[str, object]:
    """Read AQSI's current order/receipt projection without submitting another order."""
    _require_spike_admin(user, settings)
    if not reference.startswith("CORE-SPIKE-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid spike reference.")
    try:
        return gateway.get_pending_order(reference)
    except AqsiApiError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        ) from exc


@router.post(
    "/direct",
    response_model=DirectAttemptRead,
    status_code=status.HTTP_201_CREATED,
)
def create_direct_attempt(
    data: SpikeOrderRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
    gateway: Annotated[AqsiPendingOrderGateway, Depends(get_spike_gateway)],
) -> DirectAttemptRead:
    """Start exactly one explicitly confirmed real acquiring operation."""
    _require_spike_admin(user, settings)
    device_id, tax_system_code = _direct_configuration(settings)
    reference = f"CORE-DIRECT-{data.request_id.hex[:20]}"
    with _submission_lock:
        if reference in _direct_attempts:
            raise HTTPException(status.HTTP_409_CONFLICT, "This payment attempt already exists.")
    lines = _resolve_lines(session, data.lines)
    total = sum((line.line_total for line in lines), Decimal("0.00"))
    attempt = DirectAttempt(
        reference=reference,
        device_id=device_id,
        total_kopecks=_money_to_kopecks(total),
        tax_system_code=tax_system_code,
        tax_code=settings.aqsi_tax_code,
        ttl_millis=settings.aqsi_sale_spike_operation_ttl_ms,
        lines=lines,
        purchase_submission_started=True,
    )
    with _submission_lock:
        if reference in _direct_attempts:
            raise HTTPException(status.HTTP_409_CONFLICT, "This payment attempt already exists.")
        _direct_attempts[reference] = attempt
    logger.warning(
        "Submitting real AQSI direct purchase reference=%s device=%s amount=%s",
        reference,
        device_id,
        attempt.total_kopecks,
    )
    try:
        created = gateway.create_purchase(
            {
                "deviceId": device_id,
                "ttlMillis": attempt.ttl_millis,
                "amount": attempt.total_kopecks,
                "mode": "card_only",
                "printCount": 0,
            }
        )
    except AqsiApiError as exc:
        attempt.aqsi_problem = f"{exc.code}: {exc}"
        attempt.state = "unknown" if exc.outcome_unknown else "payment_failed"
        attempt.message = (
            "Статус оплаты неизвестен. Не повторяйте оплату; проверьте AQSI."
            if exc.outcome_unknown
            else (
                "Оплата не запущена: Core не смог подключиться к AQSI. "
                "После восстановления сети подтвердите новую попытку."
            )
        )
        return _attempt_read(attempt)
    operation_id = created.get("operationId")
    if not isinstance(operation_id, str) or not operation_id:
        attempt.state = "unknown"
        attempt.message = "Статус оплаты неизвестен. Не повторяйте оплату; проверьте AQSI."
        attempt.aqsi_problem = "AQSI did not return a purchase operationId."
        return _attempt_read(attempt)
    attempt.purchase_operation_id = operation_id
    attempt.purchase_status = "Pending"
    logger.warning(
        "AQSI direct purchase submitted reference=%s operation=%s",
        reference,
        operation_id,
    )
    return _attempt_read(attempt)


@router.post("/direct/{reference}/progress", response_model=DirectAttemptRead)
def progress_direct_attempt(
    reference: str,
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
    gateway: Annotated[AqsiPendingOrderGateway, Depends(get_spike_gateway)],
) -> DirectAttemptRead:
    """Poll real status and submit fiscalization once after confirmed payment."""
    _require_spike_admin(user, settings)
    if not reference.startswith("CORE-DIRECT-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid attempt reference.")
    with _submission_lock:
        attempt = _direct_attempts.get(reference)
        if attempt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Direct attempt is not in this process.")
        if attempt.progressing:
            return _attempt_read(attempt)
        if attempt.state in {
            "payment_failed",
            "payment_success_fiscalization_failed",
            "completed",
        }:
            return _attempt_read(attempt)
        attempt.progressing = True
    try:
        _advance_direct_attempt(attempt, gateway)
    except AqsiApiError as exc:
        attempt.aqsi_problem = f"{exc.code}: {exc}"
        if attempt.slip is not None:
            attempt.state = "payment_success_fiscalization_unknown"
            attempt.message = "Оплата прошла, статус фискализации неизвестен. Не повторяйте оплату."
        else:
            attempt.state = "unknown"
            attempt.message = "Статус оплаты неизвестен. Не повторяйте оплату; проверьте AQSI."
    finally:
        with _submission_lock:
            attempt.progressing = False
    return _attempt_read(attempt)

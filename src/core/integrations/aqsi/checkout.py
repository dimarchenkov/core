from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import cast

from core.config import Settings
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.sales.enums import PaymentMethod
from core.sales.providers import (
    FiscalRequest,
    PaymentRequest,
    ProviderCallError,
    ProviderHealth,
    ProviderOperation,
    ProviderOutcomeState,
)

MONEY = Decimal("0.01")
logger = logging.getLogger(__name__)
ACQUIRING_MODE_LABELS = {
    "card_only": "Только карта",
    "sbp_with_card": "Карта / QR",
    "sbp_only": "Только QR",
}


class AqsiCheckoutAdapter:
    """AQSI Cloud implementation of generic payment and fiscal provider ports."""

    def __init__(self, settings: Settings, client: AqsiHttpClient) -> None:
        """Create an adapter from validated Integration-backed settings."""
        self._settings = settings
        self._client = client
        self._device_id = int(settings.aqsi_sale_spike_device_id or "")
        self._tax_system_code = int(settings.aqsi_sale_spike_tax_system_code or 0)

    @property
    def payment_display_name(self) -> str:
        """Return a provider-mapped operator label without leaking AQSI enum values."""
        return ACQUIRING_MODE_LABELS[self._settings.aqsi_acquiring_mode]

    def initiate_payment(self, request: PaymentRequest) -> ProviderOperation:
        """Start card acquiring on the configured physical AQSI device."""
        try:
            created = self._client.create_purchase(
                {
                    "deviceId": self._device_id,
                    "ttlMillis": self._settings.aqsi_sale_spike_operation_ttl_ms,
                    "amount": _money_to_kopecks(request.amount),
                    "mode": self._settings.aqsi_acquiring_mode,
                    "printCount": 0,
                }
            )
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        operation_id = created.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            raise ProviderCallError(
                "missing_operation_id",
                "AQSI did not return a payment operation ID.",
                outcome_unknown=True,
            )
        return ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id=operation_id,
            metadata={"provider_status": "Pending"},
        )

    def get_payment(self, external_id: str, request: PaymentRequest) -> ProviderOperation:
        """Map an AQSI acquiring operation to a transport-neutral payment result."""
        try:
            operation = self._client.get_operation(external_id)
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        status = str(operation.get("status") or "")
        metadata = _safe_metadata(operation, status)
        if status == "Completed":
            try:
                slip = _completed_slip(operation, _money_to_kopecks(request.amount))
            except ValueError as exc:
                return ProviderOperation(
                    ProviderOutcomeState.UNKNOWN,
                    external_id=external_id,
                    metadata=metadata,
                    error_code="invalid_payment_result",
                    error_message=str(exc),
                )
            return ProviderOperation(
                ProviderOutcomeState.SUCCEEDED,
                external_id=external_id,
                external_reference=str(slip["id"]),
                metadata=metadata,
                fiscal_evidence=slip,
            )
        if status == "Canceled":
            return ProviderOperation(
                ProviderOutcomeState.CANCELED,
                external_id=external_id,
                metadata=metadata,
                error_code="aqsi_canceled",
                error_message=_operation_problem(operation),
            )
        if status in {"Timeout", "Error"}:
            return ProviderOperation(
                ProviderOutcomeState.FAILED,
                external_id=external_id,
                metadata=metadata,
                error_code=f"aqsi_{status.casefold()}",
                error_message=_operation_problem(operation),
            )
        if status in {"Pending", "Processing", "Finishing"}:
            return ProviderOperation(
                ProviderOutcomeState.PENDING,
                external_id=external_id,
                metadata=metadata,
            )
        return ProviderOperation(
            ProviderOutcomeState.UNKNOWN,
            external_id=external_id,
            metadata=metadata,
            error_code="unknown_provider_status",
            error_message="AQSI returned an unknown payment status.",
        )

    def check_health(self) -> ProviderHealth:
        """Check AQSI Cloud reachability; this does not prove physical terminal readiness."""
        checked_at = datetime.now(UTC).isoformat()
        try:
            self._client.list_shop_ids()
        except AqsiApiError as exc:
            return ProviderHealth(False, checked_at, str(exc))
        return ProviderHealth(
            True,
            checked_at,
            "AQSI Cloud доступен; физическая готовность кассы не подтверждается API.",
        )

    def cancel_device_operation(self, external_id: str) -> dict[str, object]:
        """Cancel one AQSI device command without requesting an acquiring reversal."""
        return self._client.cancel_operation(external_id)

    def list_receipts(
        self, *, processed_from: datetime, processed_to: datetime
    ) -> list[dict[str, object]]:
        """Expose the documented read-only receipt export for supervised recovery."""
        return self._client.list_receipts(processed_from=processed_from, processed_to=processed_to)

    def list_slips(
        self, *, processed_from: datetime, processed_to: datetime
    ) -> list[dict[str, object]]:
        """Expose the documented read-only slip export for supervised recovery."""
        return self._client.list_slips(processed_from=processed_from, processed_to=processed_to)

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Start an itemized receipt and return its ID before any status polling."""
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            created = self._client.create_receipt(self._receipt_payload(request))
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        response_at = datetime.now(UTC)
        request_ms = (perf_counter() - started) * 1000
        operation_id = created.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            raise ProviderCallError(
                "missing_operation_id",
                "AQSI did not return a fiscal operation ID.",
                outcome_unknown=True,
            )
        timing: dict[str, object] = {
            "receipt_process_started_at": started_at.isoformat(),
            "receipt_process_response_at": response_at.isoformat(),
            "receipt_process_http_ms": round(request_ms, 1),
        }
        logger.info(
            "Checkout timing sale=%s stage=T1 receipt_operation=%s receipt_process_ms=%.1f",
            request.sale_id,
            operation_id,
            request_ms,
        )
        # Persist the operation ID before a separate, non-mutating status query.
        # This cannot make AQSI + PostgreSQL atomic, but it minimizes the window
        # in which an accepted external command has no durable local identity.
        return ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id=operation_id,
            metadata={**timing, "provider_status": "Accepted"},
        )

    def find_fiscalization(self, request: FiscalRequest) -> ProviderOperation | None:
        """Recover a card receipt by its unique acquiring slip before creating one."""
        if request.payment_method is PaymentMethod.CASH:
            return None
        evidence = request.payment_evidence
        if not isinstance(evidence, dict):
            raise ProviderCallError(
                "missing_payment_evidence",
                "AQSI payment evidence is unavailable for receipt recovery.",
                outcome_unknown=True,
            )
        slip_id = evidence.get("id")
        if not isinstance(slip_id, str) or not slip_id:
            raise ProviderCallError(
                "missing_payment_slip",
                "AQSI acquiring slip ID is unavailable for receipt recovery.",
                outcome_unknown=True,
            )
        acquired_at = _slip_datetime(evidence)
        if acquired_at is None:
            raise ProviderCallError(
                "missing_payment_timestamp",
                "AQSI acquiring timestamp is unavailable for receipt recovery.",
                outcome_unknown=True,
            )
        try:
            receipt = self._client.find_receipt_by_slip(slip_id, acquired_at=acquired_at)
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        if receipt is None:
            return None
        return _verified_existing_receipt(receipt, request, self._device_id)

    def get_fiscalization(self, external_id: str, request: FiscalRequest) -> ProviderOperation:
        """Map an AQSI receipt operation without creating another receipt."""
        started = perf_counter()
        try:
            operation = self._client.get_operation(external_id)
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        elapsed_ms = (perf_counter() - started) * 1000
        extra: dict[str, object] = {"fiscal_status_http_ms": round(elapsed_ms, 1)}
        if operation.get("status") == "Completed":
            extra["fiscal_completed_observed_at"] = datetime.now(UTC).isoformat()
        logger.info(
            "Checkout timing sale=%s stage=T3 receipt_operation=%s status=%s http_ms=%.1f",
            request.sale_id,
            external_id,
            operation.get("status"),
            elapsed_ms,
        )
        return _fiscal_operation(external_id, operation, extra_metadata=extra)

    def _receipt_payload(self, request: FiscalRequest) -> dict[str, object]:
        if request.payment_method is PaymentMethod.CARD and not isinstance(
            request.payment_evidence, dict
        ):
            raise ProviderCallError(
                "missing_payment_evidence",
                "AQSI payment evidence is unavailable for fiscalization.",
                outcome_unknown=False,
            )
        positions: list[dict[str, object]] = []
        receipt_total = 0
        for line in request.lines:
            price_kopecks = _money_to_kopecks(line.unit_price)
            line_total = price_kopecks * line.quantity
            if line_total != _money_to_kopecks(line.line_total):
                raise ProviderCallError(
                    "invalid_frozen_total",
                    "Frozen SaleItem totals do not match the fiscal receipt.",
                    outcome_unknown=False,
                )
            receipt_total += line_total
            positions.append(
                {
                    "externalId": f"{line.item_id}:{line.allocation_index}",
                    "info": {
                        "calculationTypeId": 4,
                        "calculationSubjectId": 1,
                        "name": line.name[:128],
                        "taxRateId": self._settings.aqsi_tax_code,
                        "quantityUnitText": "Штука",
                        "quantityUnitId": 0,
                        "baseQuantity": str(line.quantity),
                        "finalPrice": price_kopecks,
                    },
                    **({"barcodes": [line.barcode]} if line.barcode else {}),
                }
            )
        expected = _money_to_kopecks(request.amount)
        if receipt_total != expected:
            raise ProviderCallError(
                "invalid_frozen_total",
                "Frozen Sale total does not match the fiscal receipt.",
                outcome_unknown=False,
            )
        payment = (
            {"type": 0, "amount": expected}
            if request.payment_method is PaymentMethod.CASH
            else {"type": 1, "amount": expected, "slip": request.payment_evidence}
        )
        return {
            "deviceId": self._device_id,
            "ttlMillis": self._settings.aqsi_sale_spike_operation_ttl_ms,
            "typeId": 1,
            "info": {"taxSystemCode": self._tax_system_code},
            "positions": positions,
            "payments": [payment],
            "ignoreItemCodeCheck": False,
            "skipPrinting": False,
        }


def _fiscal_operation(
    external_id: str,
    operation: dict[str, object],
    *,
    extra_metadata: dict[str, object] | None = None,
) -> ProviderOperation:
    """Map an AQSI receipt operation while retaining safe timing diagnostics."""
    status = str(operation.get("status") or "")
    metadata = {**_safe_metadata(operation, status), **(extra_metadata or {})}
    if status == "Completed":
        return ProviderOperation(
            ProviderOutcomeState.SUCCEEDED,
            external_id=external_id,
            external_reference=_result_reference(operation),
            metadata=metadata,
        )
    if status in {"Canceled", "Timeout", "Error"}:
        return ProviderOperation(
            ProviderOutcomeState.FAILED,
            external_id=external_id,
            metadata=metadata,
            error_code=f"aqsi_{status.casefold()}",
            error_message=_operation_problem(operation),
        )
    if status in {"Pending", "Processing", "Finishing"}:
        return ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id=external_id,
            metadata=metadata,
        )
    return ProviderOperation(
        ProviderOutcomeState.UNKNOWN,
        external_id=external_id,
        metadata=metadata,
        error_code="unknown_provider_status",
        error_message="AQSI returned an unknown fiscalization status.",
    )


def _provider_error(error: AqsiApiError) -> ProviderCallError:
    """Translate AQSI errors while preserving only sanitized diagnostics."""
    return ProviderCallError(error.code, str(error), outcome_unknown=error.outcome_unknown)


def _money_to_kopecks(value: Decimal) -> int:
    return int((value.quantize(MONEY) * 100).to_integral_exact())


def _completed_slip(operation: dict[str, object], expected_amount: int) -> dict[str, object]:
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


def _slip_datetime(slip: dict[str, object]) -> datetime | None:
    """Read the acquiring timestamp used to bound the receipt lookup."""
    content = slip.get("content")
    candidates = [
        slip.get("processedAtTz"),
        content.get("dateTime") if isinstance(content, dict) else None,
        slip.get("createdAt"),
    ]
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed
    return None


def _verified_existing_receipt(
    receipt: dict[str, object], request: FiscalRequest, device_id: int
) -> ProviderOperation:
    """Accept only a fiscal receipt whose immutable facts match this Sale."""
    info = receipt.get("info")
    device = receipt.get("device")
    receipt_id = receipt.get("id")
    expected = _money_to_kopecks(request.amount)
    valid_device = not isinstance(device, dict) or device.get("id") in {None, device_id}
    if (
        not isinstance(receipt_id, str)
        or not receipt_id
        or receipt.get("isNonFiscal") is not False
        or not isinstance(info, dict)
        or info.get("sum") != expected
        or not valid_device
    ):
        raise ProviderCallError(
            "existing_receipt_unverified",
            "An AQSI receipt references this slip but does not match the frozen Sale.",
            outcome_unknown=True,
        )
    document = info.get("docInfo")
    if not isinstance(document, dict) or not all(
        document.get(key) for key in ("docNumber", "fiscalStorageNumber", "docFiscalAttributeInt")
    ):
        raise ProviderCallError(
            "existing_receipt_unverified",
            "AQSI returned a receipt without complete fiscal document attributes.",
            outcome_unknown=True,
        )
    return ProviderOperation(
        ProviderOutcomeState.SUCCEEDED,
        external_reference=receipt_id,
        metadata={
            "provider_status": "Completed",
            "recovered_existing_receipt": True,
            "fiscal_document_number": document["docNumber"],
            "fiscal_storage_number": document["fiscalStorageNumber"],
            "fiscal_sign": document["docFiscalAttributeInt"],
        },
    )


def _operation_problem(operation: dict[str, object]) -> str | None:
    values = [operation.get("message"), operation.get("problems")]
    message = " · ".join(str(value) for value in values if value)
    return message[:1000] or None


def _safe_metadata(operation: dict[str, object], status: str) -> dict[str, object]:
    metadata: dict[str, object] = {"provider_status": status or "Unknown"}
    for source, target in (
        ("createdAt", "provider_created_at"),
        ("updatedAt", "provider_updated_at"),
    ):
        value = operation.get(source)
        if isinstance(value, str):
            metadata[target] = value
    problem = _operation_problem(operation)
    if problem:
        metadata["problem"] = problem
    return metadata


def _result_reference(operation: dict[str, object]) -> str | None:
    raw = operation.get("result")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        value = raw.get("id") or raw.get("guid") or raw.get("receiptId")
        return str(value) if value else None
    return None

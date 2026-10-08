from __future__ import annotations

import json
from decimal import Decimal
from typing import cast

from core.config import Settings
from core.integrations.aqsi.client import AqsiApiError, AqsiHttpClient
from core.sales.enums import PaymentMethod
from core.sales.providers import (
    FiscalRequest,
    PaymentRequest,
    ProviderCallError,
    ProviderOperation,
    ProviderOutcomeState,
)

MONEY = Decimal("0.01")
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

    def initiate_fiscalization(self, request: FiscalRequest) -> ProviderOperation:
        """Start an itemized receipt using frozen SaleItem snapshots."""
        try:
            created = self._client.create_receipt(self._receipt_payload(request))
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        operation_id = created.get("operationId")
        if not isinstance(operation_id, str) or not operation_id:
            raise ProviderCallError(
                "missing_operation_id",
                "AQSI did not return a fiscal operation ID.",
                outcome_unknown=True,
            )
        return ProviderOperation(
            ProviderOutcomeState.PENDING,
            external_id=operation_id,
            metadata={"provider_status": "Pending"},
        )

    def get_fiscalization(self, external_id: str, request: FiscalRequest) -> ProviderOperation:
        """Map an AQSI receipt operation without creating another receipt."""
        del request
        try:
            operation = self._client.get_operation(external_id)
        except AqsiApiError as exc:
            raise _provider_error(exc) from exc
        status = str(operation.get("status") or "")
        metadata = _safe_metadata(operation, status)
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


def _operation_problem(operation: dict[str, object]) -> str | None:
    values = [operation.get("message"), operation.get("problems")]
    message = " · ".join(str(value) for value in values if value)
    return message[:1000] or None


def _safe_metadata(operation: dict[str, object], status: str) -> dict[str, object]:
    metadata: dict[str, object] = {"provider_status": status or "Unknown"}
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

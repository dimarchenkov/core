from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx2

from core.config import Settings
from core.integrations.aqsi.schemas import (
    AqsiDefaultCategoryPayload,
    AqsiGoodsPayload,
    AqsiShopPricePayload,
)


class AqsiApiError(Exception):
    """Raised when AQSI returns a definitive HTTP or response error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        """Create a sanitized integration error."""
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        super().__init__(message)


class AqsiGateway(Protocol):
    """Port used by the publication processor and fake integration tests."""

    def get_good(self, external_id: str) -> dict[str, object] | None:
        """Return one AQSI good or None when it does not exist."""

    def list_goods(self) -> list[dict[str, object]]:
        """Return the complete account-wide AQSI goods catalog."""

    def create_good(self, payload: AqsiGoodsPayload) -> None:
        """Submit one AQSI good for creation."""

    def update_good(self, payload: AqsiGoodsPayload) -> None:
        """Submit one AQSI good for update."""

    def category_exists(self, category_id: str) -> bool:
        """Return whether an active AQSI goods category exists."""

    def create_category(self, payload: AqsiDefaultCategoryPayload) -> None:
        """Submit the default AQSI category for creation."""

    def list_shop_ids(self) -> list[str]:
        """Return active AQSI shop identifiers visible to the account."""

    def set_shop_price(self, payload: AqsiShopPricePayload) -> None:
        """Bind one good to a shop and set its retail price."""


class AqsiPendingOrderGateway(Protocol):
    """Temporary port for the isolated deferred-order and checkout spike."""

    def create_pending_order(self, payload: dict[str, object]) -> dict[str, object]:
        """Create or update one AQSI V2 deferred order."""

    def get_pending_order(self, order_id: str) -> dict[str, object]:
        """Read one AQSI V2 deferred order by its external ID."""

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Create one AQSI acquiring purchase device operation."""

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Read one AQSI device operation and its real status."""

    def cancel_operation(self, operation_id: str) -> dict[str, object]:
        """Request cancellation of one device operation without reversing its payment."""

    def create_receipt(self, payload: dict[str, object]) -> dict[str, object]:
        """Create one AQSI fiscal receipt device operation."""


class AqsiHttpClient:
    """Small AQSI V2 HTTP adapter with sanitized failures."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Create a client from secret-bearing runtime settings."""
        if settings.aqsi_api_key is None:
            raise AqsiApiError("not_configured", "AQSI API key is not configured.")
        self._client = httpx2.Client(
            base_url=settings.aqsi_base_url.rstrip("/"),
            headers={
                "x-client-key": f"Application {settings.aqsi_api_key.get_secret_value()}",
                "Accept": "application/json",
            },
            timeout=settings.aqsi_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> AqsiHttpClient:
        """Enter a managed AQSI client context."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the managed AQSI client context."""
        self.close()

    def get_good(self, external_id: str) -> dict[str, object] | None:
        """Read one AQSI good by the Core-owned external ID."""
        response = self._request("GET", f"/v2/Goods/{external_id}", allow_not_found=True)
        if response is None:
            return None
        value = response.json()
        if not isinstance(value, dict):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid goods response.")
        return value

    def list_goods(self) -> list[dict[str, object]]:
        """Read every page of the account-wide AQSI goods catalog."""
        page_number = 0
        goods: list[dict[str, object]] = []
        while True:
            response = self._request(
                "GET",
                "/v2/Goods/list",
                params={"pageNumber": page_number},
            )
            if response is None:
                raise AqsiApiError("invalid_response", "AQSI returned an empty goods list.")
            value = response.json()
            if not isinstance(value, Mapping):
                raise AqsiApiError("invalid_response", "AQSI returned an invalid goods list.")
            rows = value.get("rows")
            pages = value.get("pages")
            if not isinstance(rows, list) or not isinstance(pages, int) or pages < 0:
                raise AqsiApiError("invalid_response", "AQSI returned an invalid goods list.")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise AqsiApiError(
                        "invalid_response",
                        "AQSI returned an invalid goods list row.",
                    )
                if not isinstance(row.get("id"), str) or not row["id"]:
                    raise AqsiApiError(
                        "invalid_response",
                        "AQSI returned a goods list row without an identifier.",
                    )
                goods.append(dict(row))
            page_number += 1
            if page_number >= max(pages, 1):
                return goods
            if page_number > 10_000:
                raise AqsiApiError(
                    "invalid_response",
                    "AQSI returned an unreasonable goods page count.",
                )

    def create_good(self, payload: AqsiGoodsPayload) -> None:
        """Queue creation of one AQSI good."""
        self._request("POST", "/v2/Goods", json=payload.as_aqsi_json())

    def update_good(self, payload: AqsiGoodsPayload) -> None:
        """Queue update of one AQSI good."""
        self._request("PUT", "/v2/Goods", json=payload.as_aqsi_json())

    def category_exists(self, category_id: str) -> bool:
        """Return whether AQSI lists the active default category."""
        response = self._request("GET", "/v2/GoodsCategory/list")
        if response is None:
            return False
        value = response.json()
        if not isinstance(value, list):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid category list.")
        return any(
            isinstance(item, Mapping)
            and item.get("id") == category_id
            and item.get("deletedAt") is None
            for item in value
        )

    def create_category(self, payload: AqsiDefaultCategoryPayload) -> None:
        """Queue creation of the deterministic default AQSI category."""
        self._request("POST", "/v2/GoodsCategory", json=payload.as_aqsi_json())

    def list_shop_ids(self) -> list[str]:
        """Return active shops available for goods price binding."""
        response = self._request("GET", "/v2/Shops/list")
        if response is None:
            return []
        value = response.json()
        if not isinstance(value, list):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid shop list.")
        return [
            str(item["id"])
            for item in value
            if isinstance(item, Mapping)
            and item.get("id") is not None
            and item.get("deletedAt") is None
        ]

    def set_shop_price(self, payload: AqsiShopPricePayload) -> None:
        """Bind one good to its shop and apply the current retail price."""
        self._request("POST", "/v2/Goods/prices", json=[payload.as_aqsi_json()])

    def create_pending_order(self, payload: dict[str, object]) -> dict[str, object]:
        """Submit one temporary itemized deferred order."""
        response = self._request("POST", "/v2/Orders/simple", json=payload)
        if response is None:
            raise AqsiApiError("invalid_response", "AQSI returned an empty order response.")
        value = response.json()
        if not isinstance(value, dict):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid order response.")
        return value

    def get_pending_order(self, order_id: str) -> dict[str, object]:
        """Read one temporary deferred order without creating another operation."""
        response = self._request("GET", f"/v2/Orders/simple/{order_id}")
        if response is None:
            raise AqsiApiError("invalid_response", "AQSI returned an empty order response.")
        value = response.json()
        if not isinstance(value, dict):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid order response.")
        return value

    def create_purchase(self, payload: dict[str, object]) -> dict[str, object]:
        """Start a card acquiring operation on the configured physical device."""
        return self._device_operation("POST", "/v4/Slips/process/purchase", payload)

    def get_operation(self, operation_id: str) -> dict[str, object]:
        """Read the authoritative AQSI state for one device operation."""
        return self._device_operation("GET", f"/v4/Operations/{operation_id}")

    def cancel_operation(self, operation_id: str) -> dict[str, object]:
        """Use AQSI's documented device-operation cancellation endpoint."""
        return self._device_operation("POST", f"/v4/Operations/{operation_id}/cancel")

    def list_receipts(
        self, *, processed_from: datetime, processed_to: datetime
    ) -> list[dict[str, object]]:
        """Read all fiscal receipts in one explicit half-open provider time range."""
        return self._list_processed_entities(
            "/v4/Receipts", processed_from=processed_from, processed_to=processed_to
        )

    def list_slips(
        self, *, processed_from: datetime, processed_to: datetime
    ) -> list[dict[str, object]]:
        """Read all acquiring slips in one explicit half-open provider time range."""
        return self._list_processed_entities(
            "/v4/Slips", processed_from=processed_from, processed_to=processed_to
        )

    def find_receipt_by_slip(
        self, slip_id: str, *, acquired_at: datetime
    ) -> dict[str, object] | None:
        """Find a fiscal receipt linked to one unique acquiring slip without creating it."""
        start = acquired_at.astimezone(UTC) - timedelta(minutes=5)
        end = datetime.now(UTC) + timedelta(minutes=5)
        matches: list[dict[str, object]] = []
        for row in self.list_receipts(processed_from=start, processed_to=end):
            payments = row.get("payments")
            if not isinstance(payments, list):
                continue
            if any(_receipt_payment_has_slip(payment, slip_id) for payment in payments):
                matches.append(row)
        if len(matches) > 1:
            raise AqsiApiError(
                "duplicate_receipts",
                "AQSI returned more than one receipt for the acquiring slip.",
                outcome_unknown=True,
            )
        return matches[0] if matches else None

    def _list_processed_entities(
        self,
        path: str,
        *,
        processed_from: datetime,
        processed_to: datetime,
    ) -> list[dict[str, object]]:
        """Page through one documented v4 list without mutating provider state."""
        if (
            processed_from.tzinfo is None
            or processed_to.tzinfo is None
            or processed_from >= processed_to
        ):
            raise ValueError("AQSI export range must be timezone-aware and increasing.")
        page = 1
        records: list[dict[str, object]] = []
        while True:
            response = self._request(
                "GET",
                path,
                params={
                    "pageSize": 100,
                    "page": page,
                    "filtered.processedAtTzFrom": processed_from.isoformat(),
                    "filtered.processedAtTzTo": processed_to.isoformat(),
                },
            )
            if response is None:
                raise AqsiApiError("invalid_response", "AQSI returned an empty list.")
            value = response.json()
            if not isinstance(value, Mapping):
                raise AqsiApiError("invalid_response", "AQSI returned an invalid list.")
            rows = value.get("rows")
            pages = value.get("pages")
            if not isinstance(rows, list) or not isinstance(pages, int) or pages < 0:
                raise AqsiApiError("invalid_response", "AQSI returned an invalid list.")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise AqsiApiError("invalid_response", "AQSI returned an invalid list row.")
                records.append(dict(row))
            page += 1
            if page > max(pages, 1):
                return records
            if page > 10_000:
                raise AqsiApiError("invalid_response", "AQSI returned an unreasonable page count.")

    def create_receipt(self, payload: dict[str, object]) -> dict[str, object]:
        """Start fiscalization of an itemized receipt on the physical device."""
        return self._device_operation("POST", "/v4/Receipts/process", payload)

    def _device_operation(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        response = self._request(method, path, json=payload)
        if response is None:
            raise AqsiApiError("invalid_response", "AQSI returned an empty operation response.")
        value = response.json()
        if not isinstance(value, dict):
            raise AqsiApiError("invalid_response", "AQSI returned an invalid operation response.")
        return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: Mapping[str, object] | None = None,
        allow_not_found: bool = False,
    ) -> httpx2.Response | None:
        """Send one request and convert failures into safe domain errors."""
        try:
            response = self._client.request(method, path, json=json, params=params)
        except httpx2.ConnectTimeout as exc:
            raise AqsiApiError(
                "connect_timeout",
                "Could not connect to AQSI before the request was sent.",
                retryable=True,
            ) from exc
        except httpx2.ConnectError as exc:
            raise AqsiApiError(
                "connect_error",
                "Could not connect to AQSI before the request was sent.",
                retryable=True,
            ) from exc
        except httpx2.TimeoutException as exc:
            raise AqsiApiError(
                "timeout",
                "AQSI request timed out.",
                retryable=True,
                outcome_unknown=True,
            ) from exc
        except httpx2.RequestError as exc:
            raise AqsiApiError(
                "network_error",
                "AQSI request failed.",
                retryable=True,
                outcome_unknown=True,
            ) from exc

        if allow_not_found and response.status_code in {400, 404}:
            return None
        if 200 <= response.status_code < 300:
            return response

        code = f"http_{response.status_code}"
        message = "AQSI rejected the request."
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            remote_code = body.get("code")
            errors = body.get("errors")
            reason = body.get("reason")
            remote_message = body.get("message")
            if isinstance(remote_code, str) and remote_code:
                code = remote_code[:128]
            elif isinstance(reason, str) and reason:
                code = reason[:128]
            if isinstance(errors, list) and errors:
                message = "; ".join(str(item) for item in errors)[:1000]
            elif isinstance(remote_message, str) and remote_message:
                message = remote_message[:1000]
        server_error = response.status_code >= 500
        mutating_request = method.upper() not in {"GET", "HEAD", "OPTIONS"}
        raise AqsiApiError(
            code,
            message,
            retryable=server_error,
            # A 5xx response proves only that AQSI could not return a successful
            # response.  For a financial command it does not prove that the
            # command was not accepted before the server failed.
            outcome_unknown=server_error and mutating_request,
        )


def _receipt_payment_has_slip(payment: object, slip_id: str) -> bool:
    """Return whether one receipt payment references the exact acquiring slip."""
    if not isinstance(payment, Mapping):
        return False
    slip = payment.get("slip")
    return isinstance(slip, Mapping) and slip.get("id") == slip_id

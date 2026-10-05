from __future__ import annotations

from collections.abc import Mapping
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
        allow_not_found: bool = False,
    ) -> httpx2.Response | None:
        """Send one request and convert failures into safe domain errors."""
        try:
            response = self._client.request(method, path, json=json)
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
        raise AqsiApiError(code, message, retryable=response.status_code >= 500)

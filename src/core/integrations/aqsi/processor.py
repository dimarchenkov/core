from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.orm import Session

from core.config import Settings
from core.integrations.aqsi.client import AqsiApiError, AqsiGateway
from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationOperation,
    PublicationStatus,
)
from core.integrations.aqsi.payload import AqsiPayloadBuilder
from core.integrations.aqsi.repository import PublicationAttemptRepository, PublicationRepository
from core.integrations.aqsi.schemas import AqsiGoodsPayload, AqsiShopPricePayload
from core.shared.db import UUIDv7


class AqsiPublicationProcessor:
    """Execute and verify one persisted AQSI publication attempt."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        gateway: AqsiGateway,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a processor with an injectable AQSI gateway and sleeper."""
        self._session = session
        self._settings = settings
        self._gateway = gateway
        self._sleeper = sleeper
        self._publications = PublicationRepository(session)
        self._attempts = PublicationAttemptRepository(session)
        self._payload_builder = AqsiPayloadBuilder(session, settings)

    def process(self, attempt_id: UUIDv7) -> None:
        """Send, then verify, one AQSI product projection."""
        attempt = self._attempts.get(attempt_id, for_update=True)
        if attempt is None:
            return
        publication = self._publications.get(attempt.publication_id, for_update=True)
        if publication is None or attempt.status in {
            PublicationAttemptStatus.PROCESSING,
            PublicationAttemptStatus.PUBLISHED,
        }:
            return

        payload_snapshot = dict(attempt.payload)
        external_id = publication.external_id
        attempt.status = PublicationAttemptStatus.PROCESSING
        self._session.commit()

        try:
            payload = AqsiGoodsPayload.model_validate(payload_snapshot)
            category = self._payload_builder.build_default_category()
            self._ensure_category(category)
            shop_id = self._resolve_shop_id()

            remote = self._gateway.get_good(external_id)
            if (
                remote is not None
                and self._matches(remote, payload)
                and self._is_bound_to_shop(remote, shop_id)
            ):
                self._mark_published(publication, attempt)
                return

            if remote is None or not self._matches(remote, payload):
                if remote is None:
                    attempt.operation = PublicationOperation.CREATE
                    self._gateway.create_good(payload)
                else:
                    attempt.operation = PublicationOperation.UPDATE
                    self._gateway.update_good(payload)
                self._mark_accepted(publication, attempt)
                remote = self._wait_for_good(external_id, payload)

            self._gateway.set_shop_price(
                AqsiShopPricePayload.for_good(payload.id, shop_id, payload.price)
            )
            remote = self._wait_for_shop_binding(external_id, payload, shop_id)
            if self._matches(remote, payload):
                self._mark_published(publication, attempt)
        except (AqsiApiError, ValidationError) as exc:
            code = exc.code if isinstance(exc, AqsiApiError) else "invalid_payload"
            self._mark_failed(publication, attempt, code, str(exc))
            if isinstance(exc, AqsiApiError) and exc.retryable:
                raise

    def _ensure_category(self, category: object) -> None:
        """Create and verify the deterministic default AQSI category."""
        from core.integrations.aqsi.schemas import AqsiDefaultCategoryPayload

        if not isinstance(category, AqsiDefaultCategoryPayload):
            raise AqsiApiError("invalid_category", "AQSI category payload is invalid.")
        if self._gateway.category_exists(category.id):
            return
        self._gateway.create_category(category)
        for verification_number in range(self._settings.aqsi_verification_attempts):
            if self._gateway.category_exists(category.id):
                return
            if verification_number + 1 < self._settings.aqsi_verification_attempts:
                self._sleeper(self._settings.aqsi_verification_interval_seconds)
        raise AqsiApiError(
            "category_verification_timeout",
            "AQSI category could not be verified.",
            retryable=True,
        )

    def _resolve_shop_id(self) -> str:
        """Use an explicit shop or safely discover the account's only active shop."""
        shop_ids = self._gateway.list_shop_ids()
        configured = self._settings.aqsi_shop_id
        if configured is not None:
            if configured not in shop_ids:
                raise AqsiApiError("shop_not_found", "Configured AQSI shop was not found.")
            return configured
        if len(shop_ids) == 1:
            return shop_ids[0]
        if not shop_ids:
            raise AqsiApiError("shop_not_found", "AQSI account has no active shop.")
        raise AqsiApiError(
            "shop_not_configured",
            "AQSI account has multiple shops; configure one shop ID.",
        )

    def _wait_for_good(
        self,
        external_id: str,
        payload: AqsiGoodsPayload,
    ) -> dict[str, object]:
        """Poll until AQSI exposes the authoritative goods fields."""
        for verification_number in range(self._settings.aqsi_verification_attempts):
            remote = self._gateway.get_good(external_id)
            if remote is not None and self._matches(remote, payload):
                return remote
            if verification_number + 1 < self._settings.aqsi_verification_attempts:
                self._sleeper(self._settings.aqsi_verification_interval_seconds)
        raise AqsiApiError(
            "verification_timeout",
            "AQSI good could not be verified.",
            retryable=True,
        )

    def _wait_for_shop_binding(
        self,
        external_id: str,
        payload: AqsiGoodsPayload,
        shop_id: str,
    ) -> dict[str, object]:
        """Poll until the good is visible in the selected AQSI shop."""
        for verification_number in range(self._settings.aqsi_verification_attempts):
            remote = self._gateway.get_good(external_id)
            if (
                remote is not None
                and self._matches(remote, payload)
                and self._is_bound_to_shop(remote, shop_id)
            ):
                return remote
            if verification_number + 1 < self._settings.aqsi_verification_attempts:
                self._sleeper(self._settings.aqsi_verification_interval_seconds)
        raise AqsiApiError(
            "shop_verification_timeout",
            "AQSI shop binding could not be verified.",
            retryable=True,
        )

    def _mark_accepted(self, publication: object, attempt: object) -> None:
        """Persist that AQSI queued the external operation."""
        from core.integrations.aqsi.models import Publication, PublicationAttempt

        if not isinstance(publication, Publication) or not isinstance(attempt, PublicationAttempt):
            return
        now = datetime.now(UTC)
        attempt.status = PublicationAttemptStatus.ACCEPTED
        attempt.accepted_at = now
        publication.status = PublicationStatus.ACCEPTED
        self._session.commit()

    def _mark_published(self, publication: object, attempt: object) -> None:
        """Persist successful remote verification."""
        from core.integrations.aqsi.models import Publication, PublicationAttempt

        if not isinstance(publication, Publication) or not isinstance(attempt, PublicationAttempt):
            return
        now = datetime.now(UTC)
        attempt.status = PublicationAttemptStatus.PUBLISHED
        attempt.accepted_at = attempt.accepted_at or now
        attempt.completed_at = now
        attempt.error_code = None
        attempt.error_message = None
        publication.status = PublicationStatus.PUBLISHED
        publication.last_verified_payload_hash = attempt.payload_hash
        publication.last_error = None
        publication.published_at = now
        self._session.commit()

    def _mark_failed(
        self,
        publication: object,
        attempt: object,
        code: str,
        message: str,
    ) -> None:
        """Persist one sanitized terminal failure without affecting Core data."""
        from core.integrations.aqsi.models import Publication, PublicationAttempt

        if not isinstance(publication, Publication) or not isinstance(attempt, PublicationAttempt):
            return
        safe_message = message[:1000]
        attempt.status = PublicationAttemptStatus.FAILED
        attempt.error_code = code[:128]
        attempt.error_message = safe_message
        attempt.completed_at = datetime.now(UTC)
        publication.status = PublicationStatus.FAILED
        publication.last_error = safe_message
        self._session.commit()

    @staticmethod
    def _matches(remote: dict[str, object], expected: AqsiGoodsPayload) -> bool:
        """Compare the authoritative subset that Core owns in AQSI."""
        expected_json = expected.as_aqsi_json()
        fields = (
            "id",
            "group_id",
            "type",
            "name",
            "sku",
            "barcodes",
        )
        if any(remote.get(field) != expected_json.get(field) for field in fields):
            return False
        if AqsiPublicationProcessor._normalize_unit(remote.get("unit")) != (
            AqsiPublicationProcessor._normalize_unit(expected_json["unit"])
        ):
            return False
        for field in ("tax", "unitCode", "subject", "paymentMethodType"):
            try:
                if int(remote.get(field, -1)) != int(expected_json[field]):
                    return False
            except (TypeError, ValueError):
                return False
        try:
            return float(remote.get("price", -1)) == expected.price
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _normalize_unit(value: object) -> str:
        """Treat AQSI's canonical piece abbreviation as equal to our display name."""
        normalized = str(value).strip().casefold()
        if normalized in {"штука", "шт", "шт."}:
            return "piece"
        return normalized

    @staticmethod
    def _is_bound_to_shop(remote: dict[str, object], shop_id: str) -> bool:
        """Return whether AQSI reports the good in the selected active shop."""
        shops = remote.get("shops")
        if not isinstance(shops, list):
            return False
        return any(
            isinstance(shop, dict)
            and str(shop.get("id")) == shop_id
            and shop.get("deletedAt") is None
            for shop in shops
        )

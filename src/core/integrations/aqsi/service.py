from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.config import Settings
from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationChannel,
    PublicationOperation,
    PublicationStatus,
)
from core.integrations.aqsi.models import Publication, PublicationAttempt
from core.integrations.aqsi.payload import (
    AqsiPayloadBuilder,
    AqsiVariantNotFoundError,
    AqsiVariantNotReadyError,
)
from core.integrations.aqsi.repository import PublicationAttemptRepository, PublicationRepository
from core.shared.db import UUIDv7


class AqsiIntegrationDisabledError(Exception):
    """Raised when an operator requests an integration that is disabled."""


class AqsiIntegrationNotConfiguredError(Exception):
    """Raised when required AQSI credentials are absent."""


class PublicationNotFoundError(Exception):
    """Raised when no AQSI publication exists for a Variant."""


class PublicationAttemptNotFoundError(Exception):
    """Raised when a queued publication attempt cannot be found."""


class AqsiPublicationService:
    """Accept idempotent, attributed AQSI product publication commands."""

    def __init__(self, session: Session, settings: Settings) -> None:
        """Create a service using current persistence and configuration."""
        self._session = session
        self._settings = settings
        self._publications = PublicationRepository(session)
        self._attempts = PublicationAttemptRepository(session)
        self._variants = CatalogVariantRepository(session)
        self._payload_builder = AqsiPayloadBuilder(session, settings)

    def request_publication(
        self,
        variant_id: UUIDv7,
        *,
        actor_id: UUIDv7,
    ) -> tuple[Publication, PublicationAttempt, bool]:
        """Persist one publication command or return its active duplicate."""
        self._ensure_configured()
        if self._variants.get_for_update(variant_id) is None:
            raise AqsiVariantNotFoundError
        payload = self._payload_builder.build_goods(variant_id)
        payload_hash = self._payload_builder.canonical_hash(payload)

        publication = self._publications.get_for_variant(
            variant_id,
            PublicationChannel.AQSI,
            for_update=True,
        )
        if publication is None:
            publication = Publication(
                variant_id=variant_id,
                channel=PublicationChannel.AQSI,
                external_id=str(variant_id),
                status=PublicationStatus.PENDING,
                created_by_id=actor_id,
            )
            self._publications.add(publication)
            self._session.flush()
        else:
            active_attempt = self._attempts.latest_pending(publication.id)
            if active_attempt is not None and active_attempt.payload_hash == payload_hash:
                return publication, active_attempt, False
            if publication.last_verified_payload_hash == payload_hash:
                latest_attempt = self._attempts.latest(publication.id)
                if latest_attempt is not None:
                    return publication, latest_attempt, False

        operation = (
            PublicationOperation.UPDATE
            if publication.last_verified_payload_hash is not None
            else PublicationOperation.CREATE
        )
        attempt = PublicationAttempt(
            publication_id=publication.id,
            operation=operation,
            status=PublicationAttemptStatus.PENDING,
            payload=payload.as_aqsi_json(),
            payload_hash=payload_hash,
            attempt_number=self._attempts.next_number(publication.id),
            requested_at=datetime.now(UTC),
            created_by_id=actor_id,
        )
        self._attempts.add(attempt)
        publication.status = PublicationStatus.PENDING
        publication.last_requested_payload_hash = payload_hash
        publication.last_error = None
        publication.updated_by_id = actor_id
        self._session.commit()
        self._session.refresh(publication)
        self._session.refresh(attempt)
        return publication, attempt, True

    def get_publication(self, variant_id: UUIDv7) -> Publication:
        """Return current AQSI publication state for a Variant."""
        publication = self._publications.get_for_variant(variant_id, PublicationChannel.AQSI)
        if publication is None:
            raise PublicationNotFoundError
        return publication

    def list_attempts(self, variant_id: UUIDv7) -> list[PublicationAttempt]:
        """Return attributed AQSI attempt history for a Variant."""
        publication = self.get_publication(variant_id)
        return list(self._attempts.list_for_publication(publication.id))

    def is_outdated(self, publication: Publication) -> bool:
        """Derive drift by comparing current Core data with the last verified payload."""
        if publication.last_verified_payload_hash is None:
            return True
        try:
            current = self._payload_builder.build_goods(publication.variant_id)
        except (AqsiVariantNotFoundError, AqsiVariantNotReadyError):
            return True
        current_hash = self._payload_builder.canonical_hash(current)
        return current_hash != publication.last_verified_payload_hash

    def mark_enqueue_failed(self, attempt_id: UUIDv7) -> None:
        """Record failure to hand a persisted request to the worker queue."""
        attempt = self._attempts.get(attempt_id, for_update=True)
        if attempt is None:
            raise PublicationAttemptNotFoundError
        publication = self._publications.get(attempt.publication_id, for_update=True)
        if publication is None:
            raise PublicationNotFoundError
        now = datetime.now(UTC)
        attempt.status = PublicationAttemptStatus.FAILED
        attempt.error_code = "queue_error"
        attempt.error_message = "Core could not enqueue the AQSI publication job."
        attempt.completed_at = now
        publication.status = PublicationStatus.FAILED
        publication.last_error = attempt.error_message
        self._session.commit()

    def _ensure_configured(self) -> None:
        """Require an explicitly enabled integration with a secret key."""
        if not self._settings.aqsi_enabled:
            raise AqsiIntegrationDisabledError
        if self._settings.aqsi_api_key is None:
            raise AqsiIntegrationNotConfiguredError

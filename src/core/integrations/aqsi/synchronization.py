from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.config import Settings
from core.integrations.aqsi.payload import (
    AqsiVariantNotFoundError,
    AqsiVariantNotReadyError,
)
from core.integrations.aqsi.service import AqsiPublicationService
from core.shared.db import UUIDv7


@dataclass(frozen=True)
class AqsiCatalogSyncPlan:
    """Persisted publication attempts produced by one catalog synchronization sweep."""

    examined: int
    skipped_not_ready: int
    unchanged: int
    attempt_ids: tuple[UUIDv7, ...]

    @property
    def queued(self) -> int:
        """Return how many worker jobs must be enqueued for this sweep."""
        return len(self.attempt_ids)


class AqsiCatalogSynchronizationService:
    """Compare operational Core Variants with their last verified AQSI projection."""

    def __init__(self, session: Session, settings: Settings) -> None:
        """Bind synchronization planning to one database transaction boundary."""
        self._variants = CatalogVariantRepository(session)
        self._publication = AqsiPublicationService(session, settings)

    def plan(
        self,
        *,
        actor_id: UUIDv7 | None,
        retry_unchanged_failures: bool,
    ) -> AqsiCatalogSyncPlan:
        """Persist idempotent attempts for every new or locally outdated ready Variant."""
        variant_ids = self._variants.list_operational_ids()
        skipped_not_ready = 0
        unchanged = 0
        attempt_ids: list[UUIDv7] = []

        for variant_id in variant_ids:
            try:
                _, attempt, should_enqueue = self._publication.request_publication(
                    variant_id,
                    actor_id=actor_id,
                    retry_unchanged_failure=retry_unchanged_failures,
                )
            except (AqsiVariantNotFoundError, AqsiVariantNotReadyError):
                skipped_not_ready += 1
                continue
            if should_enqueue:
                attempt_ids.append(attempt.id)
            else:
                unchanged += 1

        return AqsiCatalogSyncPlan(
            examined=len(variant_ids),
            skipped_not_ready=skipped_not_ready,
            unchanged=unchanged,
            attempt_ids=tuple(attempt_ids),
        )

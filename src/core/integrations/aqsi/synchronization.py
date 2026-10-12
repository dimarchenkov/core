from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.config import Settings
from core.integrations.aqsi.client import AqsiApiError, AqsiGateway
from core.integrations.aqsi.comparison import (
    aqsi_good_is_bound_to_shop,
    aqsi_good_matches,
)
from core.integrations.aqsi.enums import PublicationChannel
from core.integrations.aqsi.payload import (
    AqsiPayloadBuilder,
    AqsiVariantNotFoundError,
    AqsiVariantNotReadyError,
)
from core.integrations.aqsi.repository import PublicationRepository
from core.integrations.aqsi.schemas import AqsiGoodsPayload
from core.integrations.aqsi.service import AqsiPublicationService
from core.shared.db import UUIDv7

AQSI_DELETION_GUARD_MAX_COUNT = 5
AQSI_DELETION_GUARD_MAX_RATIO = 0.10
AQSI_RECONCILIATION_DIAGNOSTIC_ID_LIMIT = 200


@dataclass(frozen=True)
class AqsiCatalogSyncPlan:
    """Persisted repair attempts and metrics produced by one full comparison."""

    examined: int
    expected: int
    skipped_not_ready: int
    unchanged: int
    remote_total: int
    remote_managed: int
    unmanaged_remote: int
    missing_count: int
    mismatched_count: int
    deletion_candidate_count: int
    missing_external_ids: tuple[str, ...]
    mismatched_external_ids: tuple[str, ...]
    deletion_candidate_external_ids: tuple[str, ...]
    deletion_guard_triggered: bool
    attempt_ids: tuple[UUIDv7, ...]

    @property
    def queued(self) -> int:
        """Return how many worker jobs must be enqueued for this sweep."""
        return len(self.attempt_ids)

    @property
    def destructive_actions_blocked(self) -> bool:
        """Remote removal is deliberately blocked for every deletion candidate."""
        return bool(self.deletion_candidate_external_ids)

    @property
    def status(self) -> str:
        """Return the operator-facing state after planning repairs."""
        if self.deletion_candidate_external_ids:
            return "needs_attention"
        if self.missing_count or self.mismatched_count or self.attempt_ids:
            return "repairing"
        return "healthy"


class AqsiCatalogSynchronizationService:
    """Reconcile the authoritative Core projection with actual AQSI state."""

    def __init__(self, session: Session, settings: Settings) -> None:
        """Bind synchronization planning to one database transaction boundary."""
        self._variants = CatalogVariantRepository(session)
        self._publications = PublicationRepository(session)
        self._publication = AqsiPublicationService(session, settings)
        self._payload_builder = AqsiPayloadBuilder(session, settings)
        self._settings = settings

    def plan(
        self,
        *,
        actor_id: UUIDv7 | None,
        retry_unchanged_failures: bool,
        gateway: AqsiGateway | None = None,
    ) -> AqsiCatalogSyncPlan:
        """Compare full identity sets and queue only required create/update repairs."""
        variant_ids = self._variants.list_operational_ids()
        expected: dict[str, tuple[UUIDv7, AqsiGoodsPayload]] = {}
        skipped_not_ready = 0
        for variant_id in variant_ids:
            try:
                payload = self._payload_builder.build_goods(variant_id)
            except (AqsiVariantNotFoundError, AqsiVariantNotReadyError):
                skipped_not_ready += 1
                continue
            expected[payload.id] = (variant_id, payload)

        known_ids = {
            publication.external_id
            for publication in self._publications.list_for_channel(PublicationChannel.AQSI)
        }
        missing_ids: set[str] = set()
        mismatched_ids: set[str] = set()
        deletion_candidate_ids: set[str] = set()
        remote_total = 0
        remote_managed = 0
        unmanaged_remote = 0

        if gateway is not None:
            remote_rows = gateway.list_goods()
            remote_ids = {str(row["id"]) for row in remote_rows}
            expected_ids = set(expected)
            managed_ids = expected_ids | known_ids
            missing_ids = expected_ids - remote_ids
            deletion_candidate_ids = (remote_ids & known_ids) - expected_ids
            remote_managed = len(remote_ids & managed_ids)
            remote_total = len(remote_ids)
            unmanaged_remote = len(remote_ids - managed_ids)
            shop_id = self._resolve_shop_id(gateway)

            # The list endpoint compares the set efficiently. Individual reads verify
            # every Core-owned field and the selected shop binding.
            for external_id in sorted(expected_ids & remote_ids):
                remote = gateway.get_good(external_id)
                if remote is None:
                    missing_ids.add(external_id)
                    continue
                payload = expected[external_id][1]
                if not aqsi_good_matches(remote, payload) or not aqsi_good_is_bound_to_shop(
                    remote,
                    shop_id,
                ):
                    mismatched_ids.add(external_id)

        unchanged = 0
        attempt_ids: list[UUIDv7] = []
        for external_id, (variant_id, _) in expected.items():
            remote_missing = external_id in missing_ids
            force_repair = remote_missing or external_id in mismatched_ids
            try:
                _, attempt, should_enqueue = self._publication.request_publication(
                    variant_id,
                    actor_id=actor_id,
                    retry_unchanged_failure=retry_unchanged_failures or force_repair,
                    force_republish=force_repair,
                    remote_missing=remote_missing,
                )
            except (AqsiVariantNotFoundError, AqsiVariantNotReadyError):
                skipped_not_ready += 1
                continue
            if should_enqueue:
                attempt_ids.append(attempt.id)
            else:
                unchanged += 1

        expected_count = len(expected)
        candidate_count = len(deletion_candidate_ids)
        deletion_guard_triggered = candidate_count > 0 and (
            candidate_count > AQSI_DELETION_GUARD_MAX_COUNT
            or expected_count == 0
            or candidate_count / max(expected_count, 1) > AQSI_DELETION_GUARD_MAX_RATIO
        )
        return AqsiCatalogSyncPlan(
            examined=len(variant_ids),
            expected=len(expected),
            skipped_not_ready=skipped_not_ready,
            unchanged=unchanged,
            remote_total=remote_total,
            remote_managed=remote_managed,
            unmanaged_remote=unmanaged_remote,
            missing_count=len(missing_ids),
            mismatched_count=len(mismatched_ids),
            deletion_candidate_count=len(deletion_candidate_ids),
            missing_external_ids=self._bounded_ids(missing_ids),
            mismatched_external_ids=self._bounded_ids(mismatched_ids),
            deletion_candidate_external_ids=self._bounded_ids(deletion_candidate_ids),
            deletion_guard_triggered=deletion_guard_triggered,
            attempt_ids=tuple(attempt_ids),
        )

    def _resolve_shop_id(self, gateway: AqsiGateway) -> str:
        """Resolve the same explicit-or-only-shop rule used during publication."""
        shop_ids = gateway.list_shop_ids()
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

    @staticmethod
    def _bounded_ids(values: set[str]) -> tuple[str, ...]:
        """Keep deterministic diagnostics without unbounded audit rows."""
        return tuple(sorted(values)[:AQSI_RECONCILIATION_DIAGNOSTIC_ID_LIMIT])

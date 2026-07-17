from __future__ import annotations

from collections.abc import Collection, Sequence
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.inventory.enums import MovementType, SourceType
from core.inventory.models import StockMovement
from core.inventory.repository import StockMovementRepository
from core.shared.db import UUIDv7


class InventoryVariantError(Exception):
    """Raised when a movement variant is missing, archived, or inactive."""

    def __init__(self, variant_id: UUIDv7) -> None:
        """Describe the variant that cannot receive an inventory movement."""
        super().__init__(f"Catalog variant {variant_id} is missing, archived, or inactive.")


class QuantityDeltaError(Exception):
    """Raised when a movement quantity cannot represent a non-zero Decimal change."""

    def __init__(self, variant_id: UUIDv7, quantity_delta: object) -> None:
        """Describe the invalid quantity value and the affected variant."""
        super().__init__(
            f"Quantity delta for catalog variant {variant_id} must be a finite non-zero Decimal; "
            f"got {quantity_delta!r}."
        )


class MovementSourceRequiredError(Exception):
    """Raised when an inventory movement does not identify its source record."""

    def __init__(self, variant_id: UUIDv7) -> None:
        """Describe the variant whose movement is missing source context."""
        super().__init__(f"A source_id is required for inventory movement of variant {variant_id}.")


class InventoryService:
    """Controlled creation and SQL-backed querying of immutable stock movements."""

    def __init__(self, session: Session) -> None:
        """Create an inventory service using the supplied database session."""
        self._session = session
        self._repository = StockMovementRepository(session)
        self._variant_repository = CatalogVariantRepository(session)

    def create_movement(
        self,
        variant_id: UUIDv7,
        movement_type: MovementType,
        quantity_delta: Decimal | int | str,
        source_type: SourceType,
        source_id: UUIDv7 | None,
        notes: str | None = None,
        actor_id: UUIDv7 | None = None,
    ) -> StockMovement:
        """Create one attributed immutable ledger entry for an active catalog variant."""
        self._ensure_variant_is_active(variant_id)
        normalized_delta = self._normalize_quantity_delta(variant_id, quantity_delta)
        if source_id is None:
            raise MovementSourceRequiredError(variant_id)
        movement = StockMovement(
            variant_id=variant_id,
            movement_type=movement_type,
            quantity_delta=normalized_delta,
            source_type=source_type,
            source_id=source_id,
            notes=notes,
            created_by_id=actor_id,
        )
        self._repository.add(movement)
        self._session.flush()
        return movement

    def reverse_movement(
        self,
        original_movement: StockMovement,
        *,
        source_type: SourceType,
        source_id: UUIDv7 | None,
        notes: str | None = None,
        actor_id: UUIDv7 | None = None,
    ) -> StockMovement:
        """Append an inverse ledger entry without revalidating historical variant state.

        Reversals compensate an existing immutable movement.  They therefore remain
        possible after the original catalog variant has been archived or deactivated.
        """
        if source_id is None:
            raise MovementSourceRequiredError(original_movement.variant_id)
        movement = StockMovement(
            variant_id=original_movement.variant_id,
            movement_type=MovementType.REVERSAL,
            quantity_delta=-original_movement.quantity_delta,
            source_type=source_type,
            source_id=source_id,
            notes=notes,
            created_by_id=actor_id,
        )
        self._repository.add(movement)
        self._session.flush()
        return movement

    def get_balance(self, variant_id: UUIDv7) -> Decimal:
        """Return a variant balance calculated by SQL aggregation of immutable movements."""
        return self._repository.get_balance(variant_id)

    def get_balances(self, variant_ids: Collection[UUIDv7]) -> dict[UUIDv7, Decimal]:
        """Return requested variant balances from one grouped SQL aggregation query."""
        return self._repository.get_balances(variant_ids)

    def list_movements(self, variant_id: UUIDv7) -> Sequence[StockMovement]:
        """Return immutable movement history for one catalog variant."""
        return self._repository.list_for_variant(variant_id)

    def _ensure_variant_is_active(self, variant_id: UUIDv7) -> None:
        """Require a non-deleted active variant before adding a movement."""
        variant = self._variant_repository.get(variant_id)
        if variant is None or not variant.is_active:
            raise InventoryVariantError(variant_id)

    def _normalize_quantity_delta(
        self,
        variant_id: UUIDv7,
        value: Decimal | int | str,
    ) -> Decimal:
        """Convert a movement delta to Decimal and reject an otherwise meaningless zero change."""
        try:
            normalized = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise QuantityDeltaError(variant_id, value) from exc
        if not normalized.is_finite() or normalized == Decimal("0"):
            raise QuantityDeltaError(variant_id, value)
        return normalized

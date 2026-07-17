from __future__ import annotations

from sqlalchemy.orm import Session

from core.inventory.enums import SourceType
from core.inventory.repository import StockMovementRepository
from core.inventory.service import InventoryService
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt
from core.receipt.repository import ReceiptRepository
from core.receipt.service import (
    ReceiptNotFoundError,
    ReceiptNotPostedError,
    ReceiptOriginalMovementsNotFoundError,
)
from core.shared.db import UUIDv7


class ReceiptCancellationService:
    """Cancel posted receipts by appending immutable reversal movements in one transaction."""

    def __init__(self, session: Session) -> None:
        """Create a receipt cancellation service using the supplied database session."""
        self._session = session
        self._receipt_repository = ReceiptRepository(session)
        self._movement_repository = StockMovementRepository(session)
        self._inventory_service = InventoryService(session)

    def cancel_receipt(self, receipt_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> Receipt:
        """Cancel a posted receipt through compensating immutable inventory movements."""
        try:
            receipt = self._get_posted_receipt(receipt_id)
            original_movements = self._movement_repository.list_original_receipt_movements(
                receipt.id
            )
            if not original_movements:
                raise ReceiptOriginalMovementsNotFoundError
            for movement in original_movements:
                self._inventory_service.reverse_movement(
                    movement,
                    source_type=SourceType.RECEIPT,
                    source_id=receipt.id,
                    notes=f"Receipt cancellation reversal for movement {movement.id}.",
                    actor_id=actor_id,
                )
            receipt.status = ReceiptStatus.CANCELLED
            if actor_id is not None:
                receipt.updated_by_id = actor_id
            self._session.commit()
            self._session.refresh(receipt)
            return receipt
        except Exception:
            self._session.rollback()
            raise

    def _get_posted_receipt(self, receipt_id: UUIDv7) -> Receipt:
        """Lock and require a non-deleted receipt in the posted lifecycle state."""
        receipt = self._receipt_repository.get_for_update(receipt_id)
        if receipt is None:
            raise ReceiptNotFoundError
        if receipt.status is not ReceiptStatus.POSTED:
            raise ReceiptNotPostedError
        return receipt

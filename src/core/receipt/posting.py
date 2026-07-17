from __future__ import annotations

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.inventory.enums import MovementType, SourceType
from core.inventory.service import InventoryService
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt
from core.receipt.repository import ReceiptItemRepository, ReceiptRepository
from core.receipt.service import (
    ReceiptNotDraftError,
    ReceiptNotFoundError,
    ReceiptSupplierError,
    ReceiptVariantError,
)
from core.shared.db import UUIDv7
from core.supplier.repository import SupplierRepository


class ReceiptItemsRequiredError(Exception):
    """Raised when a draft receipt has no lines to post into inventory."""


class ReceiptPostingService:
    """Post validated draft receipts into immutable inventory movements in one transaction."""

    def __init__(self, session: Session) -> None:
        """Create a receipt posting service using the supplied database session."""
        self._session = session
        self._receipt_repository = ReceiptRepository(session)
        self._item_repository = ReceiptItemRepository(session)
        self._supplier_repository = SupplierRepository(session)
        self._variant_repository = CatalogVariantRepository(session)
        self._inventory_service = InventoryService(session)

    def post_receipt(self, receipt_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> Receipt:
        """Post a draft receipt by creating one immutable receipt movement for every line."""
        try:
            receipt = self._get_draft_receipt(receipt_id)
            self._ensure_supplier_is_active(receipt.supplier_id)
            items = self._item_repository.list(receipt.id)
            if not items:
                raise ReceiptItemsRequiredError
            for item in items:
                self._ensure_variant_is_active(item.variant_id)
            for item in items:
                self._inventory_service.create_movement(
                    item.variant_id,
                    MovementType.RECEIPT,
                    item.quantity,
                    SourceType.RECEIPT,
                    receipt.id,
                    actor_id=actor_id,
                )
            receipt.status = ReceiptStatus.POSTED
            if actor_id is not None:
                receipt.updated_by_id = actor_id
            self._session.commit()
            self._session.refresh(receipt)
            return receipt
        except Exception:
            self._session.rollback()
            raise

    def _get_draft_receipt(self, receipt_id: UUIDv7) -> Receipt:
        """Load a non-deleted draft receipt or raise the matching business error."""
        receipt = self._receipt_repository.get_for_update(receipt_id)
        if receipt is None:
            raise ReceiptNotFoundError
        if receipt.status is not ReceiptStatus.DRAFT:
            raise ReceiptNotDraftError
        return receipt

    def _ensure_supplier_is_active(self, supplier_id: UUIDv7) -> None:
        """Require the receipt supplier to remain active through posting time."""
        supplier = self._supplier_repository.get(supplier_id)
        if supplier is None or not supplier.is_active:
            raise ReceiptSupplierError

    def _ensure_variant_is_active(self, variant_id: UUIDv7) -> None:
        """Require every receipt line variant to remain active through posting time."""
        variant = self._variant_repository.get(variant_id)
        if variant is None or not variant.is_active:
            raise ReceiptVariantError

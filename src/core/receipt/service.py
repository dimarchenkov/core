from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy.orm import Session

from core.catalog.repository import CatalogVariantRepository
from core.receipt.enums import ReceiptStatus
from core.receipt.models import Receipt, ReceiptItem
from core.receipt.receipt_number import ReceiptNumberGenerator
from core.receipt.repository import ReceiptItemRepository, ReceiptRepository
from core.receipt.schemas import ReceiptCreate, ReceiptItemCreate, ReceiptItemUpdate, ReceiptUpdate
from core.shared.db import UUIDv7
from core.shared.money import quantize_money
from core.supplier.repository import SupplierRepository


class ReceiptNotFoundError(Exception):
    """Raised when a receipt is missing or soft-deleted."""


class ReceiptItemNotFoundError(Exception):
    """Raised when a receipt line is missing or soft-deleted."""


class ReceiptSupplierError(Exception):
    """Raised when a receipt supplier is missing, archived, or inactive."""


class ReceiptVariantError(Exception):
    """Raised when a receipt item variant is missing, archived, or inactive."""


class ReceiptNotDraftError(Exception):
    """Raised when an operation is attempted on a non-draft receipt."""


class ReceiptNotPostedError(Exception):
    """Raised when an operation requires a receipt in the posted lifecycle state."""


class ReceiptOriginalMovementsNotFoundError(Exception):
    """Raised when a posted receipt has no original receipt ledger movements to reverse."""


class ReceiptService:
    """Business operations for draft supplier receipts without inventory posting."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._repository = ReceiptRepository(session)
        self._supplier_repository = SupplierRepository(session)

    def open_receipt(
        self,
        data: ReceiptCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Receipt:
        """Stage an empty draft receipt for the command owner to commit."""
        return self.stage_receipt(data, actor_id=actor_id)

    def stage_receipt(
        self,
        data: ReceiptCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Receipt:
        """Validate and stage a draft receipt inside a caller-owned transaction."""
        self._ensure_supplier_is_active(data.supplier_id)
        receipt = Receipt(
            number=ReceiptNumberGenerator.generate(self._repository.reserve_next_receipt_number()),
            supplier_id=data.supplier_id,
            receipt_date=data.receipt_date,
            status=ReceiptStatus.DRAFT,
            source_document_number=self._normalize_source_document_number(
                data.source_document_number
            ),
            notes=data.notes,
            created_by_id=actor_id,
        )
        self._repository.add(receipt)
        self._session.flush()
        return receipt

    def list_receipts(self) -> Sequence[Receipt]:
        """Return non-deleted receipts for normal delivery work."""
        return self._repository.list()

    def get_receipt(self, receipt_id: UUIDv7) -> Receipt:
        """Return a non-deleted receipt or raise a business error."""
        receipt = self._repository.get(receipt_id)
        if receipt is None:
            raise ReceiptNotFoundError
        return receipt

    def update_draft(
        self,
        receipt_id: UUIDv7,
        data: ReceiptUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Receipt:
        """Update mutable fields of a draft receipt without changing its number or status."""
        receipt = self.get_receipt(receipt_id)
        self._ensure_draft(receipt)
        changes = data.model_dump(exclude_unset=True)
        if "supplier_id" in changes:
            self._ensure_supplier_is_active(data.supplier_id)
            receipt.supplier_id = data.supplier_id
        if "receipt_date" in changes:
            receipt.receipt_date = data.receipt_date
        if "source_document_number" in changes:
            receipt.source_document_number = self._normalize_source_document_number(
                data.source_document_number
            )
        if "notes" in changes:
            receipt.notes = data.notes
        if actor_id is not None:
            receipt.updated_by_id = actor_id
        self._session.flush()
        return receipt

    def archive_draft(self, receipt_id: UUIDv7, *, actor_id: UUIDv7 | None = None) -> None:
        """Soft-delete an unposted draft receipt without changing inventory."""
        receipt = self.get_receipt(receipt_id)
        self._ensure_draft(receipt)
        receipt.soft_delete(actor_id)
        self._session.flush()

    def _ensure_supplier_is_active(self, supplier_id: UUIDv7 | None) -> None:
        """Require an active, non-archived supplier for a receipt."""
        if supplier_id is None:
            raise ReceiptSupplierError
        supplier = self._supplier_repository.get(supplier_id)
        if supplier is None or not supplier.is_active:
            raise ReceiptSupplierError

    def _ensure_draft(self, receipt: Receipt) -> None:
        """Reject mutation of receipt lifecycle states not supported by the draft phase."""
        if receipt.status is not ReceiptStatus.DRAFT:
            raise ReceiptNotDraftError

    def _normalize_source_document_number(self, value: str | None) -> str | None:
        """Trim optional supplier document references before storage."""
        return value.strip() if value is not None else None


class ReceiptItemService:
    """Business operations for lines on draft receipts without stock side effects."""

    def __init__(self, session: Session) -> None:
        """Create a service using the given database session."""
        self._session = session
        self._receipt_service = ReceiptService(session)
        self._repository = ReceiptItemRepository(session)
        self._variant_repository = CatalogVariantRepository(session)

    def add_item(
        self,
        receipt_id: UUIDv7,
        data: ReceiptItemCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> ReceiptItem:
        """Stage one active variant line for the command owner to commit."""
        return self.stage_item(receipt_id, data, actor_id=actor_id)

    def stage_item(
        self,
        receipt_id: UUIDv7,
        data: ReceiptItemCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> ReceiptItem:
        """Validate and stage a receipt line inside a caller-owned transaction."""
        receipt = self._receipt_service.get_receipt(receipt_id)
        self._ensure_draft(receipt)
        self._ensure_variant_is_active(data.variant_id)
        item = ReceiptItem(
            receipt_id=receipt_id,
            variant_id=data.variant_id,
            quantity=data.quantity,
            purchase_price=self._quantize_purchase_price(data.purchase_price),
            created_by_id=actor_id,
        )
        self._repository.add(item)
        self._session.flush()
        return item

    def update_item(
        self,
        receipt_id: UUIDv7,
        item_id: UUIDv7,
        data: ReceiptItemUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> ReceiptItem:
        """Update an existing receipt line while its parent receipt remains draft."""
        receipt = self._receipt_service.get_receipt(receipt_id)
        self._ensure_draft(receipt)
        item = self._get_item(receipt_id, item_id)
        changes = data.model_dump(exclude_unset=True)
        if "variant_id" in changes:
            self._ensure_variant_is_active(data.variant_id)
            item.variant_id = data.variant_id
        if "quantity" in changes:
            item.quantity = data.quantity
        if "purchase_price" in changes:
            item.purchase_price = self._quantize_purchase_price(data.purchase_price)
        if actor_id is not None:
            item.updated_by_id = actor_id
        self._session.flush()
        return item

    def remove_item(
        self,
        receipt_id: UUIDv7,
        item_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> None:
        """Soft-delete one line from a draft receipt without changing inventory."""
        receipt = self._receipt_service.get_receipt(receipt_id)
        self._ensure_draft(receipt)
        item = self._get_item(receipt_id, item_id)
        item.soft_delete(actor_id)
        self._session.flush()

    def list_items(self, receipt_id: UUIDv7) -> Sequence[ReceiptItem]:
        """Return active lines for an existing non-deleted receipt."""
        self._receipt_service.get_receipt(receipt_id)
        return self._repository.list(receipt_id)

    def _get_item(self, receipt_id: UUIDv7, item_id: UUIDv7) -> ReceiptItem:
        """Return an active receipt line or raise a business error."""
        item = self._repository.get(receipt_id, item_id)
        if item is None:
            raise ReceiptItemNotFoundError
        return item

    def _ensure_variant_is_active(self, variant_id: UUIDv7 | None) -> None:
        """Require an active, non-deleted variant for every receipt line."""
        if variant_id is None:
            raise ReceiptVariantError
        variant = self._variant_repository.get(variant_id)
        if variant is None or not variant.is_active:
            raise ReceiptVariantError

    def _ensure_draft(self, receipt: Receipt) -> None:
        """Reject line mutations for receipt states outside the draft lifecycle."""
        if receipt.status is not ReceiptStatus.DRAFT:
            raise ReceiptNotDraftError

    def _quantize_purchase_price(self, value: Decimal | None) -> Decimal:
        """Store unit purchase prices using the project's Decimal rounding convention."""
        if value is None:
            raise ValueError("Purchase price is required.")
        return quantize_money(value)

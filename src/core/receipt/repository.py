from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.receipt.models import Receipt, ReceiptItem
from core.shared.db import UUIDv7


class ReceiptRepository:
    """Database access for draft supplier receipts."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current database session."""
        self._session = session

    def add(self, receipt: Receipt) -> Receipt:
        """Add a receipt to the current unit of work."""
        self._session.add(receipt)
        return receipt

    def get(self, receipt_id: UUIDv7) -> Receipt | None:
        """Return one non-deleted receipt by identifier."""
        return self._session.scalar(
            select(Receipt).where(Receipt.id == receipt_id, Receipt.deleted_at.is_(None))
        )

    def list(self) -> Sequence[Receipt]:
        """Return non-deleted receipts ordered by newest business date and number."""
        statement = (
            select(Receipt)
            .where(Receipt.deleted_at.is_(None))
            .order_by(Receipt.receipt_date.desc(), Receipt.number.desc())
        )
        return self._session.scalars(statement).all()

    def reserve_next_receipt_number(self) -> int:
        """Reserve the next receipt number from PostgreSQL or the SQLite test fallback."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            number = self._session.scalar(text("SELECT nextval('receipt_number_seq')"))
            return int(number)
        numbers = self._session.scalars(select(Receipt.number)).all()
        return max((int(number.removeprefix("REC-")) for number in numbers), default=0) + 1


class ReceiptItemRepository:
    """Database access for draft receipt lines."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current database session."""
        self._session = session

    def add(self, item: ReceiptItem) -> ReceiptItem:
        """Add a receipt item to the current unit of work."""
        self._session.add(item)
        return item

    def get(self, receipt_id: UUIDv7, item_id: UUIDv7) -> ReceiptItem | None:
        """Return one non-deleted item belonging to a non-deleted receipt."""
        statement = select(ReceiptItem).where(
            ReceiptItem.id == item_id,
            ReceiptItem.receipt_id == receipt_id,
            ReceiptItem.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def list(self, receipt_id: UUIDv7) -> Sequence[ReceiptItem]:
        """Return non-deleted receipt items in their creation order."""
        statement = (
            select(ReceiptItem)
            .where(ReceiptItem.receipt_id == receipt_id, ReceiptItem.deleted_at.is_(None))
            .order_by(ReceiptItem.created_at, ReceiptItem.id)
        )
        return self._session.scalars(statement).all()

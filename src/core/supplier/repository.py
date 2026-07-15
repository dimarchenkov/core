from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import case, select, text
from sqlalchemy.orm import Session

from core.shared.db import UUIDv7
from core.supplier.models import Supplier


class SupplierRepository:
    """Database access for reusable purchasing supplier references."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the current database session."""
        self._session = session

    def add(self, supplier: Supplier) -> Supplier:
        """Add a supplier to the current unit of work."""
        self._session.add(supplier)
        return supplier

    def get(self, supplier_id: UUIDv7) -> Supplier | None:
        """Return one non-deleted supplier by identifier."""
        statement = select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def get_by_code(self, code: str) -> Supplier | None:
        """Return a supplier by stable code, including archived rows for uniqueness checks."""
        return self._session.scalar(select(Supplier).where(Supplier.code == code))

    def list(self) -> Sequence[Supplier]:
        """Return non-deleted suppliers ordered by display label and legal name."""
        display_label = case(
            (Supplier.display_name.is_not(None), Supplier.display_name),
            else_=Supplier.name,
        )
        statement = (
            select(Supplier)
            .where(Supplier.deleted_at.is_(None))
            .order_by(display_label, Supplier.name)
        )
        return self._session.scalars(statement).all()

    def reserve_next_supplier_code_number(self) -> int:
        """Reserve the next supplier code number from PostgreSQL or a SQLite-safe fallback."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            number = self._session.scalar(text("SELECT nextval('purchasing_supplier_code_seq')"))
            return int(number)

        codes = self._session.scalars(select(Supplier.code)).all()
        return max((int(code.removeprefix("SUP-")) for code in codes), default=0) + 1

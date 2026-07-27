from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from core.customers.enums import CustomerStatus
from core.customers.models import CustomerRecord
from core.shared.db import UUIDv7


class CustomerRepository:
    """Database access for Customer current-state projections."""

    def __init__(self, session: Session) -> None:
        """Create a repository bound to the caller's database session."""
        self._session = session

    def add(self, record: CustomerRecord) -> CustomerRecord:
        """Stage a customer record without committing."""
        self._session.add(record)
        return record

    def get(self, customer_id: UUIDv7) -> CustomerRecord | None:
        """Return a customer by technical identifier, including inactive records."""
        statement = select(CustomerRecord).where(
            CustomerRecord.id == customer_id,
            CustomerRecord.deleted_at.is_(None),
        )
        return self._session.scalar(statement)

    def search(
        self,
        query: str | None = None,
        *,
        status: CustomerStatus | None = None,
    ) -> Sequence[CustomerRecord]:
        """Search customers by number, normalized phone, or name."""
        statement = select(CustomerRecord).where(CustomerRecord.deleted_at.is_(None))
        if status is not None:
            statement = statement.where(CustomerRecord.status == status)
        normalized_query = (query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    CustomerRecord.customer_number.ilike(pattern),
                    CustomerRecord.phone.ilike(pattern),
                    CustomerRecord.full_name.ilike(pattern),
                )
            )
        return self._session.scalars(statement.order_by(CustomerRecord.full_name)).all()

    def reserve_next_customer_number(self) -> int:
        """Reserve the next customer number using PostgreSQL or a SQLite fallback."""
        if self._session.bind is not None and self._session.bind.dialect.name == "postgresql":
            number = self._session.scalar(text("SELECT nextval('customer_number_seq')"))
            return int(number)
        numbers = self._session.scalars(select(CustomerRecord.customer_number)).all()
        return max((int(number.removeprefix("CUS-")) for number in numbers), default=0) + 1

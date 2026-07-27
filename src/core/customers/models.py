from __future__ import annotations

from sqlalchemy import Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.customers.enums import CustomerStatus
from core.shared.db import BaseModel


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for database enum persistence."""
    return [member.value for member in enum_class]


class CustomerRecord(BaseModel):
    """Persisted current-state projection of one Customer aggregate."""

    __tablename__ = "customers"

    customer_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CustomerStatus] = mapped_column(
        Enum(CustomerStatus, name="customer_status", values_callable=_enum_values),
        nullable=False,
        default=CustomerStatus.ACTIVE,
        server_default=CustomerStatus.ACTIVE.value,
        index=True,
    )

    def soft_delete(self, actor_id: object | None = None) -> None:
        """Reject deletion because customers use an explicit active lifecycle."""
        del actor_id
        raise RuntimeError("Customers cannot be deleted; deactivate them instead.")

    def restore(self) -> None:
        """Reject restoration because customers are never soft-deleted."""
        raise RuntimeError("Customers cannot be restored; activate them instead.")

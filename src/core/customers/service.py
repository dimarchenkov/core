from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from core.customers.customer import Customer
from core.customers.customer_number import CustomerNumberGenerator
from core.customers.enums import CustomerStatus
from core.customers.exceptions import CustomerNotFoundError, CustomerPhoneInvalidError
from core.customers.mapper import (
    customer_from_record,
    customer_to_record,
    update_customer_record,
)
from core.customers.repository import CustomerRepository
from core.customers.schemas import CustomerCreate, CustomerUpdate
from core.shared.db import UUIDv7, generate_uuid_v7


class CustomerService:
    """Application operations for customer registration and current contacts."""

    def __init__(self, session: Session) -> None:
        """Create a service bound to the request transaction."""
        self._session = session
        self._repository = CustomerRepository(session)

    def create_customer(
        self,
        data: CustomerCreate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Customer:
        """Register an active customer with a stable system-generated number."""
        customer = Customer.create(
            customer_id=generate_uuid_v7(),
            customer_number=CustomerNumberGenerator.generate(
                self._repository.reserve_next_customer_number()
            ),
            full_name=data.full_name,
            phone=data.phone,
            email=data.email,
            note=data.note,
        )
        self._repository.add(customer_to_record(customer, actor_id=actor_id))
        self._session.commit()
        return customer

    def get_customer(self, customer_id: UUIDv7) -> Customer:
        """Return a customer, including an inactive historical customer."""
        record = self._repository.get(customer_id)
        if record is None:
            raise CustomerNotFoundError
        return customer_from_record(record)

    def search_customers(
        self,
        query: str | None = None,
        *,
        status: CustomerStatus | None = None,
    ) -> Sequence[Customer]:
        """Return customers matching number, phone, or name."""
        normalized_query = query
        if query is not None and any(character.isdigit() for character in query):
            try:
                normalized_query = Customer.normalize_phone(query)
            except CustomerPhoneInvalidError:
                normalized_query = query.strip()
        return [
            customer_from_record(record)
            for record in self._repository.search(normalized_query, status=status)
        ]

    def update_customer(
        self,
        customer_id: UUIDv7,
        data: CustomerUpdate,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Customer:
        """Update mutable current contacts through aggregate commands."""
        record = self._repository.get(customer_id)
        if record is None:
            raise CustomerNotFoundError
        customer = customer_from_record(record)
        changes = data.model_dump(exclude_unset=True)
        if "full_name" in changes:
            customer.rename(data.full_name or "")
        if "phone" in changes:
            customer.change_phone(data.phone or "")
        if "email" in changes:
            customer.change_email(data.email)
        if "note" in changes:
            customer.update_note(data.note)
        update_customer_record(record, customer, actor_id=actor_id)
        self._session.commit()
        return customer

    def activate_customer(
        self,
        customer_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Customer:
        """Reactivate a customer for new business operations."""
        return self._change_status(customer_id, activate=True, actor_id=actor_id)

    def deactivate_customer(
        self,
        customer_id: UUIDv7,
        *,
        actor_id: UUIDv7 | None = None,
    ) -> Customer:
        """Deactivate a customer without deleting identity or history."""
        return self._change_status(customer_id, activate=False, actor_id=actor_id)

    def _change_status(
        self,
        customer_id: UUIDv7,
        *,
        activate: bool,
        actor_id: UUIDv7 | None,
    ) -> Customer:
        """Apply one lifecycle transition and persist its current projection."""
        record = self._repository.get(customer_id)
        if record is None:
            raise CustomerNotFoundError
        customer = customer_from_record(record)
        if activate:
            customer.activate()
        else:
            customer.deactivate()
        update_customer_record(record, customer, actor_id=actor_id)
        self._session.commit()
        return customer

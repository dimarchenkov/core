from core.customers.customer import Customer
from core.customers.models import CustomerRecord
from core.shared.db import UUIDv7


def customer_to_record(
    customer: Customer,
    *,
    actor_id: UUIDv7 | None = None,
) -> CustomerRecord:
    """Map a new Customer aggregate into its persistence projection."""
    return CustomerRecord(
        id=customer.id,
        customer_number=customer.customer_number,
        full_name=customer.full_name,
        phone=customer.phone,
        email=customer.email,
        note=customer.note,
        status=customer.status,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
        created_by_id=actor_id,
    )


def update_customer_record(
    record: CustomerRecord,
    customer: Customer,
    *,
    actor_id: UUIDv7 | None = None,
) -> None:
    """Copy mutable aggregate state into an existing persistence projection."""
    record.full_name = customer.full_name
    record.phone = customer.phone
    record.email = customer.email
    record.note = customer.note
    record.status = customer.status
    record.updated_at = customer.updated_at
    record.updated_by_id = actor_id


def customer_from_record(record: CustomerRecord) -> Customer:
    """Rehydrate a Customer aggregate from persisted current state."""
    return Customer(
        record.id,
        record.customer_number,
        record.full_name,
        record.phone,
        record.email,
        record.note,
        record.status,
        record.created_at,
        record.updated_at,
    )

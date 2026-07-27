from __future__ import annotations

import re
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.customers import (
    Customer,
    CustomerEmailInvalidError,
    CustomerNameRequiredError,
    CustomerPhoneInvalidError,
    CustomerStateError,
    CustomerStatus,
)
from core.customers.admin import CustomerAdmin
from core.customers.mapper import customer_from_record
from core.customers.models import CustomerRecord
from core.customers.repository import CustomerRepository
from core.customers.schemas import CustomerCreate, CustomerUpdate
from core.customers.service import CustomerService
from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.main import create_app
from core.shared.db import Base

CUSTOMER_ID = UUID("019c0000-0000-7000-8000-000000000101")
CREATED_AT = datetime(2026, 7, 27, 12, tzinfo=UTC)


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory Customers database with audit references."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, CustomerRecord.__table__])
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide an API client using the isolated Customers database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(session: Session) -> User:
    """Create the authenticated operator used by route tests."""
    return IdentityService(session).create_admin(
        "customers-admin@example.com",
        "Customers Admin",
        "long enough password",
    )


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in an operator and build the bearer header."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_customer() -> Customer:
    """Build one deterministic aggregate for focused domain tests."""
    return Customer.create(
        customer_id=CUSTOMER_ID,
        customer_number=" CUS-000001 ",
        full_name="  Иван   Иванов ",
        phone="8 (999) 123-45-67",
        email=" USER@EXAMPLE.COM ",
        note="  Постоянный клиент  ",
        created_at=CREATED_AT,
    )


def test_customer_create_normalizes_current_contacts() -> None:
    """Registration creates an active aggregate with stable normalized data."""
    customer = create_customer()

    assert customer.customer_number == "CUS-000001"
    assert customer.full_name == "Иван Иванов"
    assert customer.phone == "+79991234567"
    assert customer.email == "user@example.com"
    assert customer.note == "Постоянный клиент"
    assert customer.status is CustomerStatus.ACTIVE
    assert customer.created_at == CREATED_AT


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("full_name", "  ", CustomerNameRequiredError),
        ("phone", "123", CustomerPhoneInvalidError),
        ("email", "broken", CustomerEmailInvalidError),
    ],
)
def test_customer_create_rejects_invalid_contacts(
    field: str,
    value: str,
    error: type[Exception],
) -> None:
    """The aggregate rejects invalid required or optional contact values."""
    data = {
        "customer_id": CUSTOMER_ID,
        "customer_number": "CUS-000001",
        "full_name": "Иван Иванов",
        "phone": "+79991234567",
        "email": None,
        "created_at": CREATED_AT,
    }
    data[field] = value

    with pytest.raises(error):
        Customer.create(**data)  # type: ignore[arg-type]


def test_customer_state_is_read_only_and_changes_through_commands() -> None:
    """Callers cannot bypass aggregate commands to mutate current contacts."""
    customer = create_customer()

    with pytest.raises(AttributeError):
        customer.phone = "+70000000000"  # type: ignore[misc]

    customer.rename("  Иван   Петров  ")
    customer.change_phone("+7 900 555-44-33")
    customer.change_email(None)
    customer.update_note(" ")
    customer.deactivate()

    assert customer.full_name == "Иван Петров"
    assert customer.phone == "+79005554433"
    assert customer.email is None
    assert customer.note is None
    assert customer.status is CustomerStatus.INACTIVE

    with pytest.raises(CustomerStateError):
        customer.deactivate()

    customer.activate()
    assert customer.status is CustomerStatus.ACTIVE


def test_customer_service_generates_numbers_and_persists_projection(session: Session) -> None:
    """Application registration persists sequential numbers without exposing them to callers."""
    service = CustomerService(session)

    first = service.create_customer(CustomerCreate(full_name="Иван Иванов", phone="89991234567"))
    second = service.create_customer(CustomerCreate(full_name="Петр Петров", phone="+79991234568"))

    assert first.customer_number == "CUS-000001"
    assert second.customer_number == "CUS-000002"
    record = CustomerRepository(session).get(first.id)
    assert record is not None
    assert customer_from_record(record).phone == "+79991234567"


def test_phone_is_searchable_but_not_unique(session: Session) -> None:
    """One normalized phone may identify several candidates for operator selection."""
    service = CustomerService(session)
    service.create_customer(CustomerCreate(full_name="Иван Иванов", phone="89991234567"))
    service.create_customer(CustomerCreate(full_name="Анна Иванова", phone="+7 999 123-45-67"))

    found = service.search_customers("8 (999) 123-45-67")

    assert [customer.full_name for customer in found] == ["Анна Иванова", "Иван Иванов"]


def test_update_and_deactivation_preserve_identity_and_history(session: Session) -> None:
    """Current contacts and status change without deleting the stable customer record."""
    service = CustomerService(session)
    customer = service.create_customer(CustomerCreate(full_name="Иван Иванов", phone="89991234567"))

    updated = service.update_customer(
        customer.id,
        CustomerUpdate(full_name="Иван Петров", email="new@example.com"),
    )
    inactive = service.deactivate_customer(customer.id)

    assert updated.id == customer.id
    assert updated.customer_number == customer.customer_number
    assert inactive.status is CustomerStatus.INACTIVE
    assert CustomerRepository(session).get(customer.id) is not None
    assert service.search_customers(status=CustomerStatus.ACTIVE) == []


def test_customer_write_schemas_forbid_identity_and_status_mutation() -> None:
    """Public payloads cannot supply stable identifiers or lifecycle state."""
    for field in ("id", "customer_number", "status"):
        assert field not in CustomerCreate.model_fields
        assert field not in CustomerUpdate.model_fields
    with pytest.raises(ValidationError):
        CustomerCreate(
            full_name="Иван Иванов",
            phone="+79991234567",
            customer_number="CUS-999999",
        )


def test_customer_routes_require_authentication(client: TestClient) -> None:
    """All Customers endpoints reject anonymous requests."""
    assert client.get("/api/customers").status_code == 401
    assert (
        client.post(
            "/api/customers",
            json={"full_name": "Иван Иванов", "phone": "+79991234567"},
        ).status_code
        == 401
    )


def test_authenticated_customer_api_supports_foundation_workflow(
    client: TestClient,
    session: Session,
    user: User,
) -> None:
    """Operator can create, search, edit, deactivate, and reactivate a customer."""
    headers = authorization_header(client, user)
    created = client.post(
        "/api/customers",
        headers=headers,
        json={
            "full_name": "  Иван   Иванов ",
            "phone": "8 (999) 123-45-67",
            "email": "USER@example.com",
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["customer_number"] == "CUS-000001"
    assert payload["phone"] == "+79991234567"
    record = session.get(CustomerRecord, UUID(payload["id"]))
    assert record is not None
    assert record.created_by_id == user.id

    found = client.get("/api/customers", headers=headers, params={"query": "999123"})
    assert found.status_code == 200
    assert [item["id"] for item in found.json()] == [payload["id"]]

    updated = client.patch(
        f"/api/customers/{payload['id']}",
        headers=headers,
        json={"note": "Проверен"},
    )
    assert updated.status_code == 200
    assert updated.json()["note"] == "Проверен"

    inactive = client.post(f"/api/customers/{payload['id']}/deactivate", headers=headers)
    assert inactive.status_code == 200
    assert inactive.json()["status"] == "inactive"
    assert (
        client.post(f"/api/customers/{payload['id']}/deactivate", headers=headers).status_code
        == 409
    )

    active = client.post(f"/api/customers/{payload['id']}/activate", headers=headers)
    assert active.status_code == 200
    assert active.json()["status"] == "active"
    session.refresh(record)
    assert record.updated_by_id == user.id


def test_customer_admin_disables_deletion_and_number_editing() -> None:
    """SQLAdmin preserves customer identity and lifecycle rules."""
    assert CustomerAdmin.can_delete is False
    assert CustomerRecord.customer_number in CustomerAdmin.form_excluded_columns


def test_customer_migration_and_metadata_are_registered() -> None:
    """Alembic and application metadata both include Customers persistence."""
    migration_path = Path("migrations/versions/0018_create_customers.py")
    content = migration_path.read_text()
    match = re.search(r'^revision: str = "([^"]+)"$', content, re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32
    assert CustomerRecord.__tablename__ in Base.metadata.tables

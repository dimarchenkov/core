from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import get_session
from core.identity.models import User
from core.identity.service import IdentityService
from core.main import create_app
from core.shared.db import Base
from core.supplier.models import Supplier
from core.supplier.schemas import SupplierCreate, SupplierUpdate
from core.supplier.service import (
    SupplierNameRequiredError,
    SupplierNotFoundError,
    SupplierService,
)


@pytest.fixture
def session() -> Generator[Session]:
    """Provide an in-memory database with identity and supplier tables."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Supplier.__table__])
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as database_session:
        yield database_session


@pytest.fixture
def client(session: Session) -> Generator[TestClient]:
    """Provide a client backed by the supplier test database."""
    app = create_app()

    def override_get_session() -> Generator[Session]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def user(session: Session) -> User:
    """Create an active account for authenticated purchasing requests."""
    return IdentityService(session).create_admin(
        "supplier-admin@example.com",
        "Supplier Admin",
        "long enough password",
    )


def authorization_header(client: TestClient, user: User) -> dict[str, str]:
    """Log in the supplied user and return its bearer authorization header."""
    response = client.post(
        "/api/auth/login",
        data={"username": user.email, "password": "long enough password"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_register_supplier_normalizes_names_and_generates_code(session: Session) -> None:
    """Supplier registration trims names and assigns the first stable system code."""
    supplier = SupplierService(session).register_supplier(
        SupplierCreate(name="  Acme LLC  ", display_name="  Acme  ")
    )

    assert supplier.name == "Acme LLC"
    assert supplier.display_name == "Acme"
    assert supplier.code == "SUP-000001"
    assert supplier.created_by_id is None
    assert supplier.updated_by_id is None
    assert supplier.deleted_by_id is None


def test_register_supplier_rejects_whitespace_only_name_without_persisting(
    session: Session,
) -> None:
    """A missing normalized supplier name is rejected before persistence."""
    with pytest.raises(SupplierNameRequiredError):
        SupplierService(session).register_supplier(SupplierCreate(name="   "))

    assert session.query(Supplier).count() == 0


def test_supplier_code_is_stable_after_update(session: Session) -> None:
    """Updating supplier details never changes the generated supplier code."""
    service = SupplierService(session)
    supplier = service.register_supplier(SupplierCreate(name="Acme"))

    updated = service.update_supplier(
        supplier.id,
        SupplierUpdate(name="Acme Supplies", display_name="  Acme Store  "),
    )

    assert updated.code == "SUP-000001"
    assert updated.name == "Acme Supplies"
    assert updated.display_name == "Acme Store"


def test_supplier_input_schemas_forbid_caller_supplied_code() -> None:
    """Public supplier write schemas cannot accept or expose code mutations."""
    assert "code" not in SupplierCreate.model_fields
    assert "code" not in SupplierUpdate.model_fields
    with pytest.raises(ValidationError):
        SupplierCreate(name="Acme", code="SUP-999999")
    with pytest.raises(ValidationError):
        SupplierUpdate(code="SUP-999999")


def test_archive_hides_supplier_from_normal_reads(session: Session) -> None:
    """Archived suppliers are soft-deleted and excluded from normal repository reads."""
    service = SupplierService(session)
    supplier = service.register_supplier(SupplierCreate(name="Acme"))

    service.archive_supplier(supplier.id)

    assert supplier.is_deleted is True
    assert service.list_suppliers() == []
    with pytest.raises(SupplierNotFoundError):
        service.get_supplier(supplier.id)


def test_supplier_service_without_actor_keeps_audit_fields_null(session: Session) -> None:
    """System supplier operations remain possible without an authenticated actor."""
    service = SupplierService(session)
    supplier = service.register_supplier(SupplierCreate(name="Acme"))
    service.update_supplier(supplier.id, SupplierUpdate(notes="Imported"))
    service.archive_supplier(supplier.id)

    assert supplier.created_by_id is None
    assert supplier.updated_by_id is None
    assert supplier.deleted_by_id is None


def test_supplier_routes_require_authentication(client: TestClient) -> None:
    """Purchasing supplier routes reject anonymous requests."""
    assert client.get("/api/purchasing/suppliers").status_code == 401
    assert client.post("/api/purchasing/suppliers", json={"name": "Acme"}).status_code == 401


def test_authenticated_supplier_routes_attribute_writes(
    client: TestClient,
    session: Session,
    user: User,
) -> None:
    """Authenticated supplier writes record one acting user through the API."""
    headers = authorization_header(client, user)
    created = client.post(
        "/api/purchasing/suppliers",
        headers=headers,
        json={"name": "  Acme LLC  ", "display_name": "  Acme  "},
    )
    assert created.status_code == 201
    supplier = session.get(Supplier, UUID(created.json()["id"]))
    assert supplier is not None
    assert supplier.code == "SUP-000001"
    assert supplier.created_by_id == user.id

    updated = client.patch(
        f"/api/purchasing/suppliers/{supplier.id}",
        headers=headers,
        json={"notes": "Preferred source"},
    )
    session.refresh(supplier)
    assert updated.status_code == 200
    assert supplier.updated_by_id == user.id
    assert updated.json()["code"] == supplier.code
    assert "created_at" in updated.json()
    assert "updated_at" in updated.json()

    archived = client.delete(f"/api/purchasing/suppliers/{supplier.id}", headers=headers)
    session.refresh(supplier)
    assert archived.status_code == 204
    assert supplier.deleted_by_id == user.id
    assert client.get("/api/purchasing/suppliers", headers=headers).json() == []
    assert (
        client.get(f"/api/purchasing/suppliers/{supplier.id}", headers=headers).status_code == 404
    )


def test_supplier_migration_revision_identifier_is_short() -> None:
    """Supplier migration revision identifiers remain within Alembic's project limit."""
    migration_path = Path("migrations/versions/0008_create_suppliers.py")
    match = re.search(r'^revision: str = "([^"]+)"$', migration_path.read_text(), re.MULTILINE)

    assert match is not None
    assert len(match.group(1)) <= 32

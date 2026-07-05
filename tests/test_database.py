from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import String, create_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from core.shared.db import (
    Base,
    BaseModel,
    SoftDeleteMixin,
    TimestampMixin,
    UserTrackingMixin,
    UUIDPrimaryKeyMixin,
    VersionMixin,
    generate_uuid_v7,
)


class _SharedFoundationModel(BaseModel):
    __tablename__ = "test_shared_foundation_model"

    name: Mapped[str] = mapped_column(String(100), nullable=False)


def test_generate_uuid_v7_returns_version_7_uuid() -> None:
    value = generate_uuid_v7()

    assert isinstance(value, UUID)
    assert value.version == 7


def test_uuid_primary_key_default_generates_uuid_v7() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[_SharedFoundationModel.__table__])

    with Session(engine) as session:
        model = _SharedFoundationModel(name="shared foundation")
        session.add(model)
        session.flush()

        assert isinstance(model.id, UUID)
        assert model.id.version == 7


def test_base_model_combines_shared_mixins() -> None:
    assert issubclass(BaseModel, Base)
    assert issubclass(BaseModel, UUIDPrimaryKeyMixin)
    assert issubclass(BaseModel, TimestampMixin)
    assert issubclass(BaseModel, SoftDeleteMixin)
    assert issubclass(BaseModel, VersionMixin)
    assert issubclass(BaseModel, UserTrackingMixin)


def test_timestamp_mixin_defines_created_and_updated_columns() -> None:
    created_at = _SharedFoundationModel.__table__.c.created_at
    updated_at = _SharedFoundationModel.__table__.c.updated_at

    assert created_at.nullable is False
    assert updated_at.nullable is False
    assert created_at.server_default is not None
    assert updated_at.server_default is not None
    assert updated_at.onupdate is not None


def test_version_mixin_defines_version_column() -> None:
    version = _SharedFoundationModel.__table__.c.version

    assert version.nullable is False
    assert version.default is not None
    assert version.server_default is not None


def test_user_tracking_mixin_defines_optional_user_columns() -> None:
    created_by_id = _SharedFoundationModel.__table__.c.created_by_id
    updated_by_id = _SharedFoundationModel.__table__.c.updated_by_id

    assert created_by_id.nullable is True
    assert updated_by_id.nullable is True


def test_soft_delete_mixin_tracks_deletion_state() -> None:
    deleted_by_id = generate_uuid_v7()
    model = _SharedFoundationModel(name="soft delete")

    assert model.is_deleted is False

    model.soft_delete(deleted_by_id=deleted_by_id)

    assert model.is_deleted is True
    assert isinstance(model.deleted_at, datetime)
    assert model.deleted_at.tzinfo is UTC
    assert model.deleted_by_id == deleted_by_id

    model.restore()

    assert model.is_deleted is False
    assert model.deleted_at is None
    assert model.deleted_by_id is None

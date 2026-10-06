from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.integrations.enums import CredentialKind, IntegrationProvider
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable values for database-backed string enums."""
    return [member.value for member in enum_class]


class Integration(BaseModel):
    """One configurable provider connection, deliberately not a global singleton."""

    __tablename__ = "integrations"

    provider: Mapped[IntegrationProvider] = mapped_column(
        Enum(
            IntegrationProvider,
            name="integration_provider",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    configuration: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    credentials: Mapped[list[IntegrationCredential]] = relationship(
        "IntegrationCredential",
        back_populates="integration",
        cascade="all, delete-orphan",
    )


class IntegrationCredential(BaseModel):
    """Authenticated-encrypted secret owned by one integration."""

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "integration_id",
            "kind",
            name="uq_integration_credentials_integration_kind",
        ),
    )

    integration_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[CredentialKind] = mapped_column(
        Enum(
            CredentialKind,
            name="integration_credential_kind",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_algorithm: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    integration: Mapped[Integration] = relationship("Integration", back_populates="credentials")

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.integrations.aqsi.enums import (
    PublicationAttemptStatus,
    PublicationChannel,
    PublicationOperation,
    PublicationStatus,
)
from core.shared.db import BaseModel, UUIDv7


def _enum_values(enum_class: type) -> list[str]:
    """Return stable string values for PostgreSQL enums."""
    return [member.value for member in enum_class]


class Publication(BaseModel):
    """Current external projection state for one Variant and channel."""

    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("channel", "variant_id", name="uq_publications_channel_variant"),
    )

    variant_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("catalog_variants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel: Mapped[PublicationChannel] = mapped_column(
        Enum(
            PublicationChannel,
            name="publication_channel",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(
            PublicationStatus,
            name="publication_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=PublicationStatus.PENDING,
        server_default=PublicationStatus.PENDING.value,
    )
    last_requested_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_verified_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list[PublicationAttempt]] = relationship(
        "PublicationAttempt",
        back_populates="publication",
        order_by="PublicationAttempt.attempt_number",
    )


class PublicationAttempt(BaseModel):
    """Audited history of one requested AQSI publication operation."""

    __tablename__ = "publication_attempts"
    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "attempt_number",
            name="uq_publication_attempts_number",
        ),
        Index("ix_publication_attempts_publication_requested", "publication_id", "requested_at"),
    )

    publication_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    operation: Mapped[PublicationOperation] = mapped_column(
        Enum(
            PublicationOperation,
            name="publication_operation",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    status: Mapped[PublicationAttemptStatus] = mapped_column(
        Enum(
            PublicationAttemptStatus,
            name="publication_attempt_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=PublicationAttemptStatus.PENDING,
        server_default=PublicationAttemptStatus.PENDING.value,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    publication: Mapped[Publication] = relationship("Publication", back_populates="attempts")

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Reject deletion because publication attempts are audit history."""
        del actor_id
        raise RuntimeError("Publication attempts cannot be deleted.")

    def restore(self) -> None:
        """Reject restoration because publication attempts cannot be deleted."""
        raise RuntimeError("Publication attempts cannot be restored.")

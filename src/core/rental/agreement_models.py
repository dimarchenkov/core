from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.shared.db import BaseModel, UUIDv7


class RentalAgreementSettingsRecord(BaseModel):
    """Current lessor facts used only when a new agreement is generated."""

    __tablename__ = "rental_agreement_settings"

    settings_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    lessor_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    lessor_inn: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    lessor_ogrnip: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    agreement_city: Mapped[str] = mapped_column(String(128), nullable=False, default="Москва")
    lessor_address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    lessor_phone: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class RentalAgreementTemplateVersionRecord(BaseModel):
    """One immutable Markdown version of the Rental agreement template."""

    __tablename__ = "rental_agreement_template_versions"

    template_version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    markdown_source: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RentalAgreementRecord(BaseModel):
    """Immutable generated agreement and its canonical final PDF artifact."""

    __tablename__ = "rental_agreements"

    rental_order_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_orders.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
        index=True,
    )
    template_version_id: Mapped[UUIDv7] = mapped_column(
        ForeignKey("rental_agreement_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    agreement_number: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rendered_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_html: Mapped[str] = mapped_column(Text, nullable=False)
    lessor_snapshot: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    items_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    attestation_method: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal_hash"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generated")

    def soft_delete(self, actor_id: UUIDv7 | None = None) -> None:
        """Generated legal artifacts are immutable and cannot be deleted."""
        del actor_id
        raise RuntimeError("Generated Rental agreements cannot be deleted.")

    def restore(self) -> None:
        """Generated legal artifacts cannot enter a deleted state."""
        raise RuntimeError("Generated Rental agreements cannot be restored.")

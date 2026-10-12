from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from core.shared.db import UUIDv7


class RentalAgreementSettingsWrite(BaseModel):
    """Editable lessor facts used for future agreement generation."""

    model_config = ConfigDict(extra="forbid")

    lessor_name: str = Field(max_length=255)
    lessor_inn: str = Field(max_length=32)
    lessor_ogrnip: str = Field(max_length=32)
    agreement_city: str = Field(max_length=128)
    lessor_address: str = Field(default="", max_length=500)
    lessor_phone: str = Field(default="", max_length=64)


class RentalAgreementSettingsRead(RentalAgreementSettingsWrite):
    """Current persisted lessor settings."""

    id: UUIDv7
    updated_at: datetime


class AgreementTemplateCreate(BaseModel):
    """Command that appends one immutable Markdown template version."""

    model_config = ConfigDict(extra="forbid")

    markdown_source: str = Field(min_length=1)
    change_note: str | None = Field(default=None, max_length=500)


class AgreementTemplatePreviewRequest(BaseModel):
    """Transient Markdown preview request that creates no history."""

    model_config = ConfigDict(extra="forbid")

    markdown_source: str = Field(min_length=1)


class AgreementTemplateVersionRead(BaseModel):
    """Immutable agreement template version metadata and source."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    template_version: int
    markdown_source: str
    content_hash: str
    change_note: str | None
    created_at: datetime


class AgreementSettingsContext(BaseModel):
    """All data required by the Rental Agreement settings screen."""

    settings: RentalAgreementSettingsRead
    current_template: AgreementTemplateVersionRead
    history: list[AgreementTemplateVersionRead]
    supported_placeholders: list[str]


class AgreementPreviewRead(BaseModel):
    """Non-persistent rendered preview for settings or one RentalOrder."""

    rendered_markdown: str
    rendered_html: str
    template_version: int | None = None
    page_count: int | None = None


class RentalAgreementRead(BaseModel):
    """Operator-facing immutable generated agreement metadata."""

    id: UUIDv7
    rental_order_id: UUIDv7
    template_version_id: UUIDv7
    template_version: int
    agreement_number: str
    generated_at: datetime
    pdf_sha256: str
    attestation_method: str
    status: str

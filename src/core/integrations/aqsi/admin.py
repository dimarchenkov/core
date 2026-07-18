from __future__ import annotations

from sqladmin import ModelView

from core.integrations.aqsi.models import Publication, PublicationAttempt


class PublicationAdmin(ModelView, model=Publication):
    """Read-only SQLAdmin view for current external projection state."""

    name = "Publication"
    name_plural = "Publications"
    icon = "fa-solid fa-cloud-arrow-up"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        Publication.variant_id,
        Publication.channel,
        Publication.status,
        Publication.external_id,
        Publication.published_at,
        Publication.last_error,
    ]


class PublicationAttemptAdmin(ModelView, model=PublicationAttempt):
    """Read-only SQLAdmin view for attributed publication attempt history."""

    name = "Publication attempt"
    name_plural = "Publication attempts"
    icon = "fa-solid fa-clock-rotate-left"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        PublicationAttempt.publication_id,
        PublicationAttempt.attempt_number,
        PublicationAttempt.operation,
        PublicationAttempt.status,
        PublicationAttempt.created_by_id,
        PublicationAttempt.requested_at,
        PublicationAttempt.error_code,
    ]

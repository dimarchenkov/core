from enum import StrEnum


class ActivityEventType(StrEnum):
    """Meaningful employee outcomes recorded during the Intake workflow."""

    INTAKE_SESSION_STARTED = "intake.session_started"
    INTAKE_ITEM_ADDED = "intake.item_added"
    INTAKE_ITEM_ABANDONED = "intake.item_abandoned"
    INTAKE_SESSION_COMPLETED = "intake.session_completed"
    INTAKE_SESSION_ABANDONED = "intake.session_abandoned"


class ActivityEntityType(StrEnum):
    """Workflow subjects supported by the first operational feed."""

    INTAKE_SESSION = "intake_session"
    INTAKE_ITEM = "intake_item"

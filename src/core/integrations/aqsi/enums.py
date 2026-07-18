from enum import StrEnum


class PublicationChannel(StrEnum):
    """External channels supported by the publication boundary."""

    AQSI = "aqsi"


class PublicationStatus(StrEnum):
    """Current state of one channel projection."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    FAILED = "failed"
    DISABLED = "disabled"


class PublicationAttemptStatus(StrEnum):
    """Lifecycle state of one requested external operation."""

    PENDING = "pending"
    PROCESSING = "processing"
    ACCEPTED = "accepted"
    PUBLISHED = "published"
    FAILED = "failed"


class PublicationOperation(StrEnum):
    """AQSI Goods operation selected for an attempt."""

    CREATE = "create"
    UPDATE = "update"

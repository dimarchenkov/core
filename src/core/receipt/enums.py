from __future__ import annotations

from enum import StrEnum


class ReceiptStatus(StrEnum):
    """Lifecycle states reserved for supplier delivery receipts."""

    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"

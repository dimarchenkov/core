from __future__ import annotations

from pydantic import BaseModel as PydanticBaseModel

from core.readiness.enums import ReadyForSaleRequirement
from core.shared.db import UUIDv7


class ReadyForSaleRead(PydanticBaseModel):
    """Derived readiness result for one sellable catalog variant."""

    variant_id: UUIDv7
    is_ready: bool
    missing_requirements: list[ReadyForSaleRequirement]

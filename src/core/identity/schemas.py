from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict

from core.shared.db import UUIDv7


class UserRead(PydanticBaseModel):
    """Safe user representation that never exposes a password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUIDv7
    email: str
    full_name: str
    is_active: bool
    is_admin: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime
    version: int


class AccessToken(PydanticBaseModel):
    """Bearer access token returned by the local identity login endpoint."""

    access_token: str
    token_type: str

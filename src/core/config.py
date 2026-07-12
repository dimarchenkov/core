from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CORE_",
        extra="ignore",
    )

    env: str = Field(default="local")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    database_url: str = Field(default="postgresql+psycopg://core:core@localhost:5432/core")
    redis_url: str = Field(default="redis://localhost:6379/0")
    storage_root: Path = Field(default=Path("storage"))
    jwt_secret: str
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_token_expire_minutes: int = Field(default=480)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings for services and integrations."""
    return Settings()

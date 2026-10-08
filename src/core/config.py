from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CORE_",
        extra="ignore",
        populate_by_name=True,
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
    master_encryption_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MASTER_ENCRYPTION_KEY", "CORE_MASTER_ENCRYPTION_KEY"),
    )
    master_encryption_key_file: Path = Field(
        default=Path("storage/secrets/master_encryption_key"),
        validation_alias=AliasChoices(
            "MASTER_ENCRYPTION_KEY_FILE",
            "CORE_MASTER_ENCRYPTION_KEY_FILE",
        ),
    )
    aqsi_enabled: bool = Field(default=False)
    aqsi_base_url: str = Field(default="https://api.aqsi.ru/pub")
    aqsi_api_key: SecretStr | None = Field(default=None)
    aqsi_tax_code: int = Field(default=6, ge=1, le=10)
    aqsi_shop_id: str | None = Field(default=None, min_length=1)
    aqsi_default_group_id: str = Field(default="core-default-goods", min_length=1)
    aqsi_default_group_name: str = Field(default="Товары Core", min_length=1, max_length=256)
    aqsi_timeout_seconds: float = Field(default=10.0, gt=0)
    aqsi_verification_attempts: int = Field(default=5, ge=1, le=20)
    aqsi_verification_interval_seconds: float = Field(default=1.0, ge=0)
    aqsi_sale_spike_enabled: bool = Field(default=False)
    aqsi_sale_spike_device_id: str | None = Field(default=None, min_length=1)
    aqsi_sale_spike_tax_system_code: int | None = Field(default=None)
    aqsi_sale_spike_operation_ttl_ms: int = Field(default=120_000, ge=30_000, le=300_000)
    aqsi_acquiring_mode: Literal["card_only", "sbp_with_card", "sbp_only"] = "sbp_with_card"
    printing_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("PRINTING_ENABLED", "CORE_PRINTING_ENABLED"),
    )
    cups_server: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("CUPS_SERVER", "CORE_CUPS_SERVER"),
    )
    cups_user: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("CUPS_USER", "CORE_CUPS_USER"),
    )
    cups_printer: str | None = Field(
        default=None,
        min_length=1,
        validation_alias=AliasChoices("CUPS_PRINTER", "CORE_CUPS_PRINTER"),
    )
    cups_ipp_version: str = Field(
        default="1.1",
        pattern=r"^\d+\.\d+$",
        validation_alias=AliasChoices("CUPS_IPP_VERSION", "CORE_CUPS_IPP_VERSION"),
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings for services and integrations."""
    from core.integrations.master_key import load_master_encryption_key

    settings = Settings()
    load_master_encryption_key(settings)
    return settings

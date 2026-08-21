"""Typed settings for foundation-service."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from converge_logging import LogFormat
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceRole(StrEnum):
    """Process roles supported by the shared service artifact."""

    all = "all"
    control = "control"
    execution = "execution"


class ServiceSettings(BaseSettings):
    """Process configuration loaded from ``FOUNDATION_*`` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FOUNDATION_",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "foundation-service"
    role: ServiceRole = ServiceRole.all
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    build_version: str = "unknown"
    web_dist_dir: Path | None = None

    database_url: str = Field(
        default="postgresql+psycopg://foundation:foundation@127.0.0.1:5432/foundation",
        min_length=1,
        repr=False,
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", min_length=1, repr=False)
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=3600, ge=0)
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    database_statement_timeout_seconds: float = Field(default=30.0, gt=0)
    database_readiness_timeout_seconds: float = Field(default=3.0, gt=0)

    auto_migrate: bool = False
    migration_advisory_lock_timeout_seconds: float = Field(default=900.0, gt=0)
    migration_lock_timeout_seconds: float = Field(default=3.0, gt=0)
    migration_statement_timeout_seconds: float = Field(default=900.0, gt=0)
    migration_idle_transaction_timeout_seconds: float = Field(default=30.0, gt=0)

    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.pretty


@lru_cache(maxsize=1)
def get_settings() -> ServiceSettings:
    """Return the process settings singleton without creating resources."""
    return ServiceSettings()

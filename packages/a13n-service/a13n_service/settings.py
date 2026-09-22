"""Strict, once-per-process configuration; environment overrides TOML."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ProcessRole = Literal["all", "control", "worker"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Server(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    readiness_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_timeout: int = Field(default=15, ge=1, le=300)


class Database(Section):
    url: SecretStr = SecretStr("postgresql+psycopg://localhost/a13n_service_rewrite")
    auto_migrate: bool = True
    pool_size: int = Field(default=5, ge=1, le=100)
    connect_timeout: int = Field(default=5, ge=1, le=60)
    statement_timeout: int = Field(default=10, ge=1, le=300)
    migration_advisory_lock_timeout: int = Field(default=900, ge=1, le=3600)
    migration_lock_timeout: int = Field(default=3, ge=1, le=60)
    migration_statement_timeout: int = Field(default=900, ge=1, le=3600)
    migration_idle_transaction_timeout: int = Field(default=30, ge=1, le=300)

    @field_validator("url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("database.url must use postgresql+psycopg")
        return value


class Settings(Section):
    server: Server = Field(default_factory=Server)
    database: Database = Field(default_factory=Database)


def load_settings(path: Path | None = None) -> Settings:
    selected = path or (Path(os.environ["A13N_SETTINGS_FILE"]) if "A13N_SETTINGS_FILE" in os.environ else None)
    values = tomllib.loads(selected.read_text()) if selected else {}
    for name, value in os.environ.items():
        if not name.startswith("A13N_") or name == "A13N_SETTINGS_FILE":
            continue
        parts = name.removeprefix("A13N_").lower().split("__")
        if len(parts) != 2 or parts[0] not in Settings.model_fields:
            raise ValueError(f"Unknown Service setting: {name}")
        section = values.setdefault(parts[0], {})
        if not isinstance(section, dict):
            raise ValueError(f"Invalid configuration section: {parts[0]}")
        section[parts[1]] = value
    return Settings.model_validate(values)

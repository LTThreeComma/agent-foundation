"""Strict, once-per-process configuration; environment overrides TOML."""

import json
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

MAX_INBOX_BYTES = 16777216

ProcessRole = Literal["all", "control", "worker"]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Server(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    request_bytes: int = Field(default=2097152, ge=1024, le=33554432)
    request_timeout: float = Field(default=10, gt=0, le=60)
    readiness_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_timeout: int = Field(default=15, ge=1, le=300)
    tls_certificate: Path | None = None
    tls_key: Path | None = None


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


class Authentication(Section):
    session_seconds: int = Field(default=43200, ge=60, le=604800)
    login_limit: int = Field(default=10, ge=1, le=1000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)


class RedisSettings(Section):
    url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    timeout: float = Field(default=2, gt=0, le=30)


class Outbound(Section):
    private_domains: tuple[str, ...] = ()
    private_cidrs: tuple[str, ...] = ()
    http_origins: tuple[str, ...] = ()
    require_https: bool = True

    @field_validator("private_domains", "private_cidrs", "http_origins", mode="before")
    @classmethod
    def parse_list(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class Encryption(Section):
    active_key_id: str | None = Field(default=None, min_length=1, max_length=128)
    keys: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("keys", mode="before")
    @classmethod
    def parse_keys(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class Objects(Section):
    root: Path = Path("var/service/objects")
    max_bytes: int = Field(default=16777216, ge=65536, le=67108864)
    timeout: float = Field(default=5, gt=0, le=60)


class Uploads(Section):
    max_bytes: int = Field(default=1048576, ge=1, le=33554432)
    limit: int = Field(default=60, ge=1, le=1000)
    window_seconds: int = Field(default=60, ge=1, le=3600)


class Control(Section):
    scan_seconds: float = Field(default=1, gt=0, le=60)
    inbox_count: int = Field(default=128, ge=1, le=10000)
    inbox_bytes: int = Field(default=1048576, ge=1024, le=MAX_INBOX_BYTES)


class Worker(Section):
    attempt_seconds: float = Field(default=3600, gt=0, le=86400)
    stream_count: int = Field(default=512, ge=1, le=4096)
    stream_bytes: int = Field(default=1048576, ge=1024, le=16777216)
    stream_entry_bytes: int = Field(default=65536, ge=1024, le=1048576)
    stream_ttl: int = Field(default=600, ge=1, le=86400)
    display_flush_seconds: float = Field(default=0.5, gt=0, le=10)
    max_events: int = Field(default=10000, ge=100, le=100000)
    slots: int = Field(default=4, ge=1, le=128)
    max_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    scan_seconds: float = Field(default=1, gt=0, le=30)
    authority_seconds: float = Field(default=1, gt=0, le=30)


class Managed(Section):
    verifier_url: str | None = Field(default=None, max_length=2048)
    return_urls: tuple[str, ...] = ()
    flow_seconds: int = Field(default=600, ge=30, le=600)
    operation_seconds: float = Field(default=10, ge=2, le=30)

    @field_validator("return_urls", mode="before")
    @classmethod
    def parse_urls(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class OAuth(Section):
    callback_url: str | None = Field(default=None, max_length=2048)
    return_urls: tuple[str, ...] = ()
    flow_seconds: int = Field(default=600, ge=30, le=1800)
    operation_seconds: float = Field(default=8, ge=2, le=30)
    scan_seconds: float = Field(default=1, gt=0, le=30)

    @field_validator("return_urls", mode="before")
    @classmethod
    def parse_urls(cls, value: object) -> object:
        return json.loads(value) if isinstance(value, str) else value


class Settings(Section):
    server: Server = Field(default_factory=Server)
    database: Database = Field(default_factory=Database)
    auth: Authentication = Field(default_factory=Authentication)
    oauth: OAuth = Field(default_factory=OAuth)
    managed: Managed = Field(default_factory=Managed)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    encryption: Encryption = Field(default_factory=Encryption)
    providers: Outbound = Field(default_factory=Outbound)
    objects: Objects = Field(default_factory=Objects)
    uploads: Uploads = Field(default_factory=Uploads)
    control: Control = Field(default_factory=Control)
    worker: Worker = Field(default_factory=Worker)


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

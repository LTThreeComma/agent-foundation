"""Strict, once-per-process configuration: one section per concern, environment overrides a TOML file.

Every limit is finite and visible here. `A13N_<SECTION>__<FIELD>` overrides a field; unknown keys fail.
"""

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ProcessRole = Literal["all", "control", "worker"]


def _json_list(value: object) -> object:
    """Environment variables carry lists and maps as JSON."""
    return json.loads(value) if isinstance(value, str) else value


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Server(Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # The externally reachable origin, used for browser redirects such as OAuth callbacks.
    public_url: str = Field(default="http://127.0.0.1:8000", max_length=2048)
    request_bytes: int = Field(default=2097152, ge=1024, le=33554432)
    request_timeout: float = Field(default=10, gt=0, le=60)
    readiness_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_timeout: int = Field(default=15, ge=1, le=300)
    tls_certificate: Path | None = None
    tls_key: Path | None = None


class Database(Section):
    url: SecretStr = SecretStr("postgresql+psycopg://localhost/a13n_service")
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


class Objects(Section):
    backend: Literal["local", "s3"] = "local"
    root: Path = Path("var/service/objects")
    bucket: str | None = None
    prefix: str = ""
    endpoint_url: str | None = None
    region: str | None = None
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    max_bytes: int = Field(default=16777216, ge=65536, le=67108864)
    timeout: float = Field(default=5, gt=0, le=60)
    upload_bytes: int = Field(default=1048576, ge=1, le=33554432)
    upload_limit: int = Field(default=60, ge=1, le=1000)
    upload_window_seconds: int = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def s3_bucket(self) -> "Objects":
        if self.backend == "s3" and not self.bucket:
            raise ValueError("objects.bucket is required for the s3 backend")
        return self


class RedisSettings(Section):
    url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
    timeout: float = Field(default=2, gt=0, le=30)


class Authentication(Section):
    session_seconds: int = Field(default=43200, ge=60, le=604800)
    login_limit: int = Field(default=10, ge=1, le=1000)
    login_window_seconds: int = Field(default=60, ge=1, le=3600)
    invitation_seconds: int = Field(default=604800, ge=3600, le=2592000)
    reset_seconds: int = Field(default=3600, ge=300, le=86400)


class Encryption(Section):
    active_key_id: str | None = Field(default=None, min_length=1, max_length=128)
    keys: dict[str, SecretStr] = Field(default_factory=dict)

    @field_validator("keys", mode="before")
    @classmethod
    def parse_keys(cls, value: object) -> object:
        return _json_list(value)


class Control(Section):
    scan_seconds: float = Field(default=1, gt=0, le=60)
    sweep_batch: int = Field(default=100, ge=1, le=10000)
    inbox_count: int = Field(default=128, ge=1, le=10000)
    inbox_bytes: int = Field(default=1048576, ge=1024, le=16777216)
    subscriptions: int = Field(default=32, ge=1, le=1000)
    outbox_batch: int = Field(default=32, ge=1, le=1000)
    outbox_attempts: int = Field(default=12, ge=1, le=100)
    outbox_retention_days: int = Field(default=14, ge=1, le=365)
    webhook_timeout: float = Field(default=10, gt=0, le=60)


class Worker(Section):
    slots: int = Field(default=4, ge=1, le=128)
    max_attempts: int = Field(default=3, ge=1, le=20)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    scan_seconds: float = Field(default=1, gt=0, le=30)
    authority_seconds: float = Field(default=1, gt=0, le=30)
    drain_seconds: float = Field(default=10, gt=0, le=300)
    # One boundary delivery batch of steers; the defaults await vertical-slice measurements.
    delivery_count: int = Field(default=8, ge=1, le=128)
    delivery_bytes: int = Field(default=262144, ge=1024, le=16777216)
    display_bytes: int = Field(default=8388608, ge=65536, le=67108864)
    output_bytes: int = Field(default=1048576, ge=1024, le=16777216)
    stream_length: int = Field(default=2048, ge=16, le=100000)
    stream_ttl: int = Field(default=600, ge=1, le=86400)
    child_depth: int = Field(default=4, ge=0, le=16)
    child_count: int = Field(default=16, ge=0, le=256)


class Environments(Section):
    scan_seconds: float = Field(default=5, gt=0, le=300)
    operation_seconds: float = Field(default=120, gt=0, le=3600)
    idle_seconds: int = Field(default=1800, ge=60, le=604800)
    # The local adapter runs commands on the worker host; it is not an isolation boundary.
    allow_local: bool = False


class Providers(Section):
    """Outbound network policy and browser authorization flows for every provider and connection."""

    private_domains: tuple[str, ...] = ()
    private_cidrs: tuple[str, ...] = ()
    http_origins: tuple[str, ...] = ()
    require_https: bool = True
    return_urls: tuple[str, ...] = ()
    flow_seconds: int = Field(default=600, ge=30, le=1800)
    operation_seconds: float = Field(default=10, ge=2, le=30)
    discovery_ttl: int = Field(default=300, ge=1, le=86400)

    @field_validator("private_domains", "private_cidrs", "http_origins", "return_urls", mode="before")
    @classmethod
    def parse_lists(cls, value: object) -> object:
        return _json_list(value)


class Telemetry(Section):
    log_format: Literal["json", "text"] = "json"


class Settings(Section):
    server: Server = Field(default_factory=Server)
    database: Database = Field(default_factory=Database)
    objects: Objects = Field(default_factory=Objects)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    auth: Authentication = Field(default_factory=Authentication)
    encryption: Encryption = Field(default_factory=Encryption)
    control: Control = Field(default_factory=Control)
    worker: Worker = Field(default_factory=Worker)
    environments: Environments = Field(default_factory=Environments)
    providers: Providers = Field(default_factory=Providers)
    telemetry: Telemetry = Field(default_factory=Telemetry)
    # Sections a distribution declares, validated by their own types.
    extensions: dict[str, Any] = Field(default_factory=dict)


def load_settings(path: Path | None = None, *, extensions: Mapping[str, type[Section]] = {}) -> Settings:
    selected = path or (Path(os.environ["A13N_SETTINGS_FILE"]) if "A13N_SETTINGS_FILE" in os.environ else None)
    values: dict[str, Any] = tomllib.loads(selected.read_text()) if selected else {}
    known = set(Settings.model_fields) - {"extensions"}
    for name, value in os.environ.items():
        if not name.startswith("A13N_") or name == "A13N_SETTINGS_FILE":
            continue
        parts = name.removeprefix("A13N_").lower().split("__")
        if len(parts) != 2 or parts[0] not in known | set(extensions):
            raise ValueError(f"Unknown Service setting: {name}")
        section = values.setdefault(parts[0], {})
        if not isinstance(section, dict):
            raise ValueError(f"Invalid configuration section: {parts[0]}")
        section[parts[1]] = value
    unknown = set(values) - known - set(extensions)
    if unknown:
        raise ValueError(f"Unknown Service settings sections: {sorted(unknown)}")
    declared = {name: kind.model_validate(values.pop(name, {})) for name, kind in extensions.items()}
    return Settings.model_validate({**values, "extensions": declared})

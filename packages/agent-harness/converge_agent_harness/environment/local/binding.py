"""Direct Local configuration and provider binding."""

from __future__ import annotations

import asyncio
import math
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..models import (
    EnvironmentAction,
    EnvironmentAvailability,
    EnvironmentBindingState,
    EnvironmentDescriptor,
    EnvironmentError,
    EnvironmentMountDescriptor,
    EnvironmentOperationFamily,
    EnvironmentPermissionSet,
)
from ..providers import BoundEnvironmentProvider, EnvironmentProviderBinding, EnvironmentProviderOperations
from .files import LocalFileOperator
from .processes import LocalPortOperator, LocalProcessManager, LocalShell
from .retention import LocalRetentionStore

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


class DirectLocalRootConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    ownership: Literal["caller_owned", "binding_owned"]
    read_only: bool = False


class DirectLocalFilePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_value_bytes: Annotated[int, Field(gt=0)] = 16 * _MIB


class DirectLocalShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    executable: Path
    fixed_arguments: tuple[str, ...] = ()
    allow_login: bool = False

    @field_validator("executable")
    @classmethod
    def _absolute_executable(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("shell profile executable must be an absolute path")
        return expanded


class DirectLocalProcessPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_executables: frozenset[Path] = frozenset()
    allowed_environment_keys: frozenset[str] = frozenset()
    max_concurrent_processes: Annotated[int, Field(gt=0)] = 128
    max_wall_time_seconds: float = 24 * 60 * 60
    terminate_grace_seconds: float = 5.0

    @model_validator(mode="after")
    def _finite_times(self) -> DirectLocalProcessPolicy:
        if not math.isfinite(self.max_wall_time_seconds) or self.max_wall_time_seconds <= 0:
            raise ValueError("max_wall_time_seconds must be positive and finite")
        if not math.isfinite(self.terminate_grace_seconds) or self.terminate_grace_seconds <= 0:
            raise ValueError("terminate_grace_seconds must be positive and finite")
        return self


class DirectLocalOutputPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_buffer_bytes: Annotated[int, Field(gt=0)] = _MIB
    max_spool_bytes: Annotated[int, Field(gt=0)] = 64 * _GIB


class DirectLocalPortPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed_ports: frozenset[Annotated[int, Field(ge=1, le=65535)]] = frozenset()


class DirectLocalEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_id: str
    root: DirectLocalRootConfiguration
    files: DirectLocalFilePolicy = DirectLocalFilePolicy()
    shell_profiles: tuple[DirectLocalShellProfile, ...] = ()
    processes: DirectLocalProcessPolicy = DirectLocalProcessPolicy()
    outputs: DirectLocalOutputPolicy = DirectLocalOutputPolicy()
    ports: DirectLocalPortPolicy = DirectLocalPortPolicy()

    @field_validator("environment_id")
    @classmethod
    def _environment_id(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("environment_id must be non-empty and bounded")
        return value

    @model_validator(mode="after")
    def _safe_combination(self) -> DirectLocalEnvironmentConfiguration:
        if self.root.read_only and (self.shell_profiles or self.processes.allowed_executables):
            raise ValueError("read-only roots cannot enable Direct Local process execution")
        profile_ids = [profile.profile_id for profile in self.shell_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("shell profile IDs must be unique")
        return self


class _BoundDirectLocalProvider(BoundEnvironmentProvider):
    def __init__(
        self,
        *,
        environment_id: str,
        generation: str,
        descriptor: EnvironmentDescriptor,
        operations: EnvironmentProviderOperations,
    ) -> None:
        self._environment_id = environment_id
        self._generation = generation
        self._descriptor = descriptor
        self._operations = operations
        self._availability = EnvironmentAvailability(
            status="available",
            ready_families=descriptor.operation_families,
        )

    @property
    def provider_type(self) -> str:
        return "converge.direct-local"

    @property
    def environment_id(self) -> str:
        return self._environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def availability(self) -> EnvironmentAvailability:
        return self._availability

    @property
    def operations(self) -> EnvironmentProviderOperations:
        return self._operations

    async def ensure_ready(self, operations: frozenset[str]) -> None:
        if not operations <= self._descriptor.operation_families:
            raise EnvironmentError("Direct Local operation family is unsupported.", code="environment_unsupported")

    async def export_state(self, *, max_bytes: int) -> EnvironmentBindingState | None:
        del max_bytes
        return None

    async def restore_state(self, state: EnvironmentBindingState) -> None:
        del state
        raise EnvironmentError("Direct Local has no portable state entry.", code="environment_state_invalid")


class DirectLocalEnvironmentProviderBinding(EnvironmentProviderBinding):
    """Single-use direct process-local root and output provider."""

    def __init__(self, configuration: DirectLocalEnvironmentConfiguration) -> None:
        self.configuration = configuration.model_copy(deep=True)
        self._used = False
        self._discarded = False

    @property
    def provider_type(self) -> str:
        return "converge.direct-local"

    @property
    def environment_id(self) -> str:
        return self.configuration.environment_id

    @asynccontextmanager
    async def bind(
        self,
        *,
        run_id: str,
        instance,
        binding_id: str,
        binding_revision: int,
    ) -> AsyncGenerator[BoundEnvironmentProvider]:
        del run_id, instance
        if self._used or self._discarded:
            raise EnvironmentError("Direct Local provider binding is single-use.", code="environment_binding_reused")
        self._used = True
        configured = self.configuration.root.path.expanduser()
        owned = self.configuration.root.ownership == "binding_owned"
        root: Path | None = None
        retention: LocalRetentionStore | None = None
        processes: LocalProcessManager | None = None
        retention_root: Path | None = None
        try:
            if owned:
                await asyncio.to_thread(configured.mkdir, parents=True, exist_ok=False)
                root = configured.resolve(strict=True)
            else:
                root = configured.resolve(strict=True)
                if not root.is_dir():
                    raise EnvironmentError(
                        "Direct Local caller root is not a directory.", code="environment_request_invalid"
                    )
            generation = f"generation-{uuid4().hex[:16]}"
            files = LocalFileOperator(
                root=root,
                read_only=self.configuration.root.read_only,
                policy=self.configuration.files,
                binding_id=binding_id,
                binding_revision=binding_revision,
                generation=generation,
            )
            process_enabled = bool(
                self.configuration.processes.allowed_executables or self.configuration.shell_profiles
            )
            if process_enabled:
                retention_root = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="converge-output-"))
                retention = LocalRetentionStore(
                    root=retention_root,
                    binding_id=binding_id,
                    binding_revision=binding_revision,
                    generation=generation,
                    max_spool_bytes=self.configuration.outputs.max_spool_bytes,
                )
                processes = LocalProcessManager(
                    files=files,
                    retention=retention,
                    policy=self.configuration.processes,
                    output_policy=self.configuration.outputs,
                    shell_profiles=self.configuration.shell_profiles,
                    binding_id=binding_id,
                    binding_revision=binding_revision,
                    generation=generation,
                )
            shell = LocalShell(processes) if processes is not None else None
            ports = LocalPortOperator(self.configuration.ports) if self.configuration.ports.allowed_ports else None
            file_actions = {action for action in EnvironmentAction if action.value.startswith("environment.file.")}
            permissions = set(file_actions)
            families: set[EnvironmentOperationFamily] = {"files"}
            if shell is not None:
                permissions.add(EnvironmentAction.SHELL_EXEC)
                families.add("shell")
            if processes is not None:
                permissions.update(
                    action for action in EnvironmentAction if action.value.startswith("environment.process.")
                )
                permissions.update({EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE})
                families.update({"processes", "outputs"})
            if ports is not None:
                permissions.update({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
                families.add("ports")
            limits: dict[str, int | float] = {
                "max_value_bytes": self.configuration.files.max_value_bytes,
            }
            if processes is not None:
                limits.update(
                    {
                        "max_wall_time_seconds": self.configuration.processes.max_wall_time_seconds,
                        "max_buffer_bytes": self.configuration.outputs.max_buffer_bytes,
                        "max_spool_bytes": self.configuration.outputs.max_spool_bytes,
                    }
                )
            descriptor = EnvironmentDescriptor(
                generation=generation,
                operation_families=frozenset(families),
                permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
                limits=limits,
                mounts=(
                    EnvironmentMountDescriptor(
                        name="root",
                        path="/",
                        read_only=self.configuration.root.read_only,
                    ),
                ),
            )
            yield _BoundDirectLocalProvider(
                environment_id=self.configuration.environment_id,
                generation=generation,
                descriptor=descriptor,
                operations=EnvironmentProviderOperations(
                    files=files,
                    shell=shell,
                    processes=processes,
                    ports=ports,
                    outputs=retention,
                ),
            )
        finally:
            try:
                if processes is not None:
                    await processes.close()
            finally:
                try:
                    if retention is not None:
                        await retention.close()
                    elif retention_root is not None:
                        await asyncio.to_thread(shutil.rmtree, retention_root, True)
                finally:
                    if owned and root is not None:
                        await asyncio.to_thread(shutil.rmtree, root, True)

    async def discard(self) -> None:
        self._discarded = True

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


class DirectLocalRootConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    ownership: Literal["caller_owned", "binding_owned"]
    read_only: bool = False


class DirectLocalFilePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_text_bytes: Annotated[int, Field(gt=0)] = 1_048_576
    max_transfer_bytes: Annotated[int, Field(gt=0)] = 64 * 1_048_576
    max_query_results: Annotated[int, Field(gt=0)] = 1_000
    max_query_bytes: Annotated[int, Field(gt=0)] = 4 * 1_048_576


class DirectLocalShellProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    executable: Path
    fixed_arguments: tuple[str, ...] = ()
    allow_login: bool = False


class DirectLocalProcessPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed_executables: frozenset[Path] = frozenset()
    allowed_environment_keys: frozenset[str] = frozenset()
    max_concurrent_processes: Annotated[int, Field(gt=0)] = 8
    max_wall_time_seconds: float = 600.0
    terminate_grace_seconds: float = 5.0
    network_mode: Literal["ambient"] = "ambient"

    @model_validator(mode="after")
    def _finite_times(self) -> DirectLocalProcessPolicy:
        if not math.isfinite(self.max_wall_time_seconds) or self.max_wall_time_seconds <= 0:
            raise ValueError("max_wall_time_seconds must be positive and finite")
        if not math.isfinite(self.terminate_grace_seconds) or self.terminate_grace_seconds <= 0:
            raise ValueError("terminate_grace_seconds must be positive and finite")
        return self


class DirectLocalRetentionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_object_bytes: Annotated[int, Field(gt=0)] = 8 * 1_048_576
    max_total_bytes: Annotated[int, Field(gt=0)] = 64 * 1_048_576
    max_objects: Annotated[int, Field(gt=0)] = 64
    max_lifetime_seconds: float = 600.0

    @model_validator(mode="after")
    def _valid_policy(self) -> DirectLocalRetentionPolicy:
        if self.max_object_bytes > self.max_total_bytes:
            raise ValueError("max_object_bytes cannot exceed max_total_bytes")
        if not math.isfinite(self.max_lifetime_seconds) or self.max_lifetime_seconds <= 0:
            raise ValueError("max_lifetime_seconds must be positive and finite")
        return self


class DirectLocalPortPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    allowed_ports: frozenset[Annotated[int, Field(ge=1, le=65535)]] = frozenset()
    address: Literal["loopback"] = "loopback"


class DirectLocalEnvironmentConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment_id: str
    root: DirectLocalRootConfiguration
    files: DirectLocalFilePolicy = DirectLocalFilePolicy()
    shell_profiles: tuple[DirectLocalShellProfile, ...] = ()
    processes: DirectLocalProcessPolicy = DirectLocalProcessPolicy()
    retention: DirectLocalRetentionPolicy = DirectLocalRetentionPolicy()
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
        allowed = {path.expanduser().resolve() for path in self.processes.allowed_executables}
        for profile in self.shell_profiles:
            if profile.executable.expanduser().resolve() not in allowed:
                raise ValueError("every shell profile executable must be allowed")
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
            retention_root = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="converge-output-"))
            files = LocalFileOperator(
                root=root,
                read_only=self.configuration.root.read_only,
                policy=self.configuration.files,
                binding_id=binding_id,
                binding_revision=binding_revision,
                generation=generation,
            )
            retention = LocalRetentionStore(
                root=retention_root,
                binding_id=binding_id,
                binding_revision=binding_revision,
                generation=generation,
                max_object_bytes=self.configuration.retention.max_object_bytes,
                max_total_bytes=self.configuration.retention.max_total_bytes,
                max_objects=self.configuration.retention.max_objects,
                max_lifetime_seconds=self.configuration.retention.max_lifetime_seconds,
            )
            process_enabled = bool(self.configuration.processes.allowed_executables)
            processes = (
                LocalProcessManager(
                    files=files,
                    retention=retention,
                    policy=self.configuration.processes,
                    shell_profiles=self.configuration.shell_profiles,
                    binding_id=binding_id,
                    binding_revision=binding_revision,
                    generation=generation,
                    max_stdin_bytes=self.configuration.files.max_transfer_bytes,
                    max_output_bytes=self.configuration.retention.max_object_bytes,
                )
                if process_enabled
                else None
            )
            shell = LocalShell(processes) if processes is not None else None
            ports = LocalPortOperator(self.configuration.ports) if self.configuration.ports.enabled else None
            file_actions = {action for action in EnvironmentAction if action.value.startswith("environment.file.")}
            output_actions = {EnvironmentAction.OUTPUT_READ, EnvironmentAction.OUTPUT_RELEASE}
            permissions = file_actions | output_actions
            families: set[EnvironmentOperationFamily] = {"files", "outputs"}
            if shell is not None:
                permissions.add(EnvironmentAction.SHELL_EXEC)
                families.add("shell")
            if processes is not None:
                permissions.update(
                    action for action in EnvironmentAction if action.value.startswith("environment.process.")
                )
                families.add("processes")
            if ports is not None:
                permissions.update({EnvironmentAction.PORT_INSPECT, EnvironmentAction.PORT_WAIT})
                families.add("ports")
            descriptor = EnvironmentDescriptor(
                generation=generation,
                operation_families=frozenset(families),
                permissions=EnvironmentPermissionSet(operations=frozenset(permissions)),
                limits={
                    "max_text_bytes": self.configuration.files.max_text_bytes,
                    "max_transfer_bytes": self.configuration.files.max_transfer_bytes,
                    "max_output_bytes": self.configuration.retention.max_object_bytes,
                },
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
            if processes is not None:
                await processes.close()
            if retention is not None:
                await retention.close()
            elif retention_root is not None:
                await asyncio.to_thread(shutil.rmtree, retention_root, True)
            if owned and root is not None:
                await asyncio.to_thread(shutil.rmtree, root, True)

    async def discard(self) -> None:
        self._discarded = True

"""Environment instances and thread mounts as the API accepts and returns them, and the stored handle/failure."""

from datetime import datetime
from typing import Annotated, Literal

from a13n_harness.providers.environment.models import EnvironmentState
from pydantic import AfterValidator, BaseModel, ConfigDict, JsonValue, StringConstraints

from a13n_service.infra.ids import ObjectId

# Desired mounts one thread holds at most; a run freezes them, plus a primary sandbox its agent reserves.
MAX_MOUNTS = 32
# The Harness mount-name rule; `workspace` is the primary mount.
MountName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]


def _canonical_directory(value: str) -> str:
    """An absolute path inside the environment's own namespace, never a route out of it."""
    segments = value[1:].split("/") if value != "/" else []
    if len(value) > 1024 or not value.startswith("/") or "\x00" in value or {"", ".", ".."} & set(segments):
        raise ValueError("working_directory must be a canonical absolute path")
    return value


WorkingDirectory = Annotated[str, AfterValidator(_canonical_directory)]
EnvironmentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
DeviceId = Annotated[str, StringConstraints(pattern=r"^[!-~]{1,128}$")]

type Certainty = Literal["not_dispatched", "known", "unknown"]


class Handle(BaseModel):
    """What reaches one instance: the recipe it was built from, which its provider state is bound to, and that
    state. A registered device has an empty recipe; a stateless provider has no state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    recipe: dict[str, JsonValue]
    state: EnvironmentState | None = None


class EnvironmentFailure(BaseModel):
    """The last error of the outstanding operation, or what refuses use of an otherwise ready instance.

    `unknown` means the call may have taken effect: only the same operation may continue. `permanent` failures
    refuse new mounts and acceptance until the cause is fixed or the instance is deleted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str
    certainty: Certainty
    permanent: bool
    operation_id: str | None
    at: datetime


class EnvironmentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    provider_id: str
    # NULL for a registered device.
    template_id: str | None
    # A registered device's native identity; NULL for a managed sandbox.
    device_id: str | None
    owner_principal_id: str | None
    name: str
    status: str
    operation_id: str | None
    operation_started_at: datetime | None
    failure: EnvironmentFailure | None
    last_used_at: datetime | None
    version: int
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class EnvironmentPage(BaseModel):
    items: list[EnvironmentView]
    next_cursor: str | None


class ManagedEnvironmentCreate(BaseModel):
    """A workspace-managed sandbox reserved from a template, for threads to mount; maintenance creates it."""

    model_config = ConfigDict(extra="forbid")
    template_id: ObjectId
    # Defaults to the template's name.
    name: EnvironmentName | None = None


class DeviceRegistration(BaseModel):
    """A connect-only device of an `http_envd` provider."""

    model_config = ConfigDict(extra="forbid")
    provider_id: ObjectId
    device_id: DeviceId
    name: EnvironmentName | None = None


class EnvironmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: EnvironmentName


class MountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: MountName
    environment_id: ObjectId
    # The mount's default directory inside the environment and the root of its route.
    working_directory: WorkingDirectory | None = None


class MountView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    environment_id: str
    working_directory: str | None


class MountPage(BaseModel):
    items: list[MountView]
    next_cursor: str | None = None

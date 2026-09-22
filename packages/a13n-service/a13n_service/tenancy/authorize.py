"""Detached grants, credential confinement and the one scope/verb rule."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from a13n_service.infra.errors import ServiceError

type Verb = Literal["read", "run", "write", "admin"]

ROLES: Mapping[str, frozenset[Verb]] = MappingProxyType(
    {
        "viewer": frozenset({"read"}),
        "runner": frozenset({"read", "run"}),
        "builder": frozenset({"read", "run", "write"}),
        "admin": frozenset({"read", "run", "write", "admin"}),
    }
)


class Scoped(Protocol):
    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class Scope:
    organization_id: str
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class Grant:
    organization_id: str
    workspace_id: str | None
    role: str


@dataclass(frozen=True, slots=True)
class Principal:
    id: str
    kind: Literal["user", "service_account"]
    grants: tuple[Grant, ...]
    confinement: Scope | None = None
    active: bool = True
    name: str = ""
    email: str | None = None


class ExecutionAuthority(BaseModel):
    """Accepted delegation ceiling; current status/grants can only narrow it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    principal_id: str = Field(min_length=1, max_length=72)
    organization_id: str = Field(min_length=1, max_length=72)
    workspace_id: str = Field(min_length=1, max_length=72)
    verbs: frozenset[Verb]


def allowed_verbs(
    principal: Principal, resource: Scoped, *, roles: Mapping[str, frozenset[Verb]] = ROLES
) -> frozenset[Verb]:
    if not principal.active:
        return frozenset()
    if any(grant.role not in roles for grant in principal.grants):
        raise ServiceError("forbidden", "Principal has an unknown role")
    confinement = principal.confinement
    if confinement is not None and (
        confinement.workspace_id is None
        or confinement.organization_id != resource.organization_id
        or (resource.workspace_id is not None and resource.workspace_id != confinement.workspace_id)
    ):
        return frozenset()
    verbs: set[Verb] = set()
    for grant in principal.grants:
        if grant.organization_id != resource.organization_id:
            continue
        permitted = roles[grant.role]
        if resource.workspace_id is None:
            verbs.update(permitted if grant.workspace_id is None else permitted & {"read", "run"})
        elif grant.workspace_id is None or grant.workspace_id == resource.workspace_id:
            verbs.update(permitted)
    if confinement is not None and resource.workspace_id is None:
        verbs.intersection_update({"read", "run"})
    return frozenset(verbs)


def authorize(
    principal: Principal,
    resource: Scoped,
    verb: Verb,
    *,
    roles: Mapping[str, frozenset[Verb]] = ROLES,
    authority: ExecutionAuthority | None = None,
) -> None:
    if authority is not None and (
        authority.principal_id != principal.id
        or authority.organization_id != resource.organization_id
        or resource.workspace_id not in {None, authority.workspace_id}
        or (resource.workspace_id is None and verb not in {"read", "run"})
        or verb not in authority.verbs
    ):
        raise ServiceError("forbidden", "Execution delegation does not cover this operation", {"verb": verb})
    if verb not in allowed_verbs(principal, resource, roles=roles):
        raise ServiceError("forbidden", "Principal cannot perform this operation", {"verb": verb})


def execution_authority(
    principal: Principal, scope: Scope, *, roles: Mapping[str, frozenset[Verb]] = ROLES
) -> ExecutionAuthority:
    if scope.workspace_id is None:
        raise ServiceError("invalid_argument", "Execution requires a workspace")
    authorize(principal, scope, "run", roles=roles)
    return ExecutionAuthority(
        principal_id=principal.id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        verbs=allowed_verbs(principal, scope, roles=roles),
    )

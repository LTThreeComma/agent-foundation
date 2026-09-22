"""Explicit composition of the implemented foundation; no package scanning."""

from dataclasses import dataclass
from pathlib import Path

from a13n_harness.providers.model import ModelProviderDefinition
from a13n_harness.providers.model.openai import DEFINITION as OPENAI
from fastapi import APIRouter
from sqlalchemy import MetaData, Table

from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import Base
from a13n_service.resources.agents.routes import router as agents_router
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.models.routes import catalog_router
from a13n_service.resources.models.routes import router as models_router
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.runs.events import EventCursorRow, EventRow
from a13n_service.runs.policy import AdmissionPolicy
from a13n_service.runs.routes import router as runs_router
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.usage import UsageRow
from a13n_service.tenancy.credentials import ApiKeyRow, TokenRow
from a13n_service.tenancy.routes import router as tenancy_router
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow, WorkspaceRow


@dataclass(frozen=True)
class Distribution:
    name: str
    tables: tuple[type[Base], ...]
    migrations: tuple[Path, ...]
    routers: tuple[APIRouter, ...] = ()
    model_providers: tuple[ModelProviderDefinition, ...] = ()
    admission: AdmissionPolicy | None = None

    def metadata(self) -> MetaData:
        metadata = MetaData(naming_convention=Base.metadata.naming_convention)
        for row in self.tables:
            table = row.__table__
            if not isinstance(table, Table):
                raise TypeError("Distribution rows must own concrete tables")
            if table.key in metadata.tables:
                raise ValueError(f"Duplicate table: {table.key}")
            table.to_metadata(metadata)
        return metadata


OSS = Distribution(
    name="oss",
    tables=(
        OrganizationRow,
        WorkspaceRow,
        PrincipalRow,
        PasswordRow,
        GrantRow,
        AuditEventRow,
        ApiKeyRow,
        TokenRow,
        ModelProviderRow,
        ModelRow,
        AgentRow,
        AgentRevisionRow,
        SessionRow,
        ThreadRow,
        InboxEntryRow,
        RunRow,
        AttemptRow,
        EventCursorRow,
        EventRow,
        UsageRow,
    ),
    routers=(tenancy_router, models_router, catalog_router, agents_router, runs_router),
    model_providers=(OPENAI,),
    migrations=(Path(__file__).parent / "migrations" / "versions",),
)

"""What a build contributes, listed explicitly: no package scanning, no import-time registration.

A distribution extends the core by `OSS.extend(Distribution(...))`; duplicate tables, routes, sweeps and
delivery kinds fail at assembly, so an extension can never silently shadow a core operation.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.model.builtins import BUILT_IN_MODEL_PROVIDERS
from fastapi import APIRouter
from sqlalchemy import MetaData, Table

from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import Base, schema_rules, table_rules
from a13n_service.infra.outbox import Claim, OutboxKind, OutboxRow
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.agents.routes import router as agents_router
from a13n_service.resources.agents.tables import AgentRevisionRow, AgentRow
from a13n_service.resources.assets.routes import router as assets_router
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow
from a13n_service.resources.environment_providers.tables import EnvironmentProviderRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.models.routes import catalog_router
from a13n_service.resources.models.routes import router as models_router
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.resources.subscriptions.tables import SubscriptionRow
from a13n_service.runs.accept import ThreadAdvancer
from a13n_service.runs.admission import AdmissionPolicy
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, InboxEntryRow, RunRow, SessionRow, ThreadRow, UsageRecordRow
from a13n_service.settings import Section
from a13n_service.tenancy.credentials import ApiKeyRow, TokenRow
from a13n_service.tenancy.routes import router as tenancy_router
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow, WorkspaceRow

# Background work and outbox handlers need the assembled runtime, so a distribution lists factories.
type SweepFactory = Callable[[Runtime], Sweep]
type DeliveryFactory = Callable[[Runtime], Callable[[Claim], Awaitable[None]]]


@dataclass(frozen=True)
class Distribution:
    name: str
    routers: tuple[APIRouter, ...] = ()
    tables: tuple[type[Base], ...] = ()
    migrations: tuple[Path, ...] = ()
    settings: Mapping[str, type[Section]] = field(default_factory=dict)
    sweeps: tuple[SweepFactory, ...] = ()
    deliveries: Mapping[OutboxKind, DeliveryFactory] = field(default_factory=dict)
    providers: tuple[ProviderDefinition, ...] = ()
    admission: AdmissionPolicy | None = None

    def extend(self, extension: "Distribution") -> "Distribution":
        for name, mine, theirs in (
            ("settings section", self.settings, extension.settings),
            ("delivery kind", self.deliveries, extension.deliveries),
        ):
            if duplicate := set(mine) & set(theirs):
                raise ValueError(f"Duplicate {name}: {sorted(duplicate)}")
        if self.admission is not None and extension.admission is not None:
            raise ValueError("Only one admission policy may be installed")
        return Distribution(
            name=extension.name,
            routers=self.routers + extension.routers,
            tables=self.tables + extension.tables,
            migrations=self.migrations + extension.migrations,
            settings=MappingProxyType({**self.settings, **extension.settings}),
            sweeps=self.sweeps + extension.sweeps,
            deliveries=MappingProxyType({**self.deliveries, **extension.deliveries}),
            providers=self.providers + extension.providers,
            admission=extension.admission or self.admission,
        )

    def metadata(self) -> MetaData:
        """The composed schema: table definitions and the rules each table declares in `info`."""
        metadata = MetaData(naming_convention=Base.metadata.naming_convention)
        for row in self.tables:
            table = row.__table__
            if not isinstance(table, Table):
                raise TypeError("Distribution rows must own concrete tables")
            if table.key in metadata.tables:
                raise ValueError(f"Duplicate table: {table.key}")
            table.to_metadata(metadata)
        return metadata

    def rules(self) -> list[str]:
        """Every rule in creation order, for schemas built from metadata (tests)."""
        return schema_rules(self.tables)

    def table_rules(self) -> dict[str, list[str]]:
        """Rules by table, for rendering into the migration that creates each table."""
        return {row.__tablename__: table_rules(row) for row in self.tables}


def _advance_threads(runtime: Runtime) -> Sweep:
    control = runtime.settings.control
    return Sweep(
        name="advance_threads",
        every=control.scan_seconds,
        run=ThreadAdvancer(runtime, batch=control.sweep_batch),
        timeout=max(30, control.scan_seconds * 10),
    )


OSS = Distribution(
    name="oss",
    tables=(
        OrganizationRow,
        WorkspaceRow,
        PrincipalRow,
        PasswordRow,
        GrantRow,
        ApiKeyRow,
        TokenRow,
        AuditEventRow,
        OutboxRow,
        ModelProviderRow,
        ModelRow,
        EnvironmentProviderRow,
        EnvironmentTemplateRow,
        ConnectionRow,
        ConnectionAuthorizationRow,
        SubscriptionRow,
        AssetRow,
        AgentRow,
        AgentRevisionRow,
        SessionRow,
        ThreadRow,
        EnvironmentRow,
        ThreadEnvironmentRow,
        InboxEntryRow,
        RunRow,
        AttemptRow,
        UsageRecordRow,
    ),
    routers=(tenancy_router, models_router, catalog_router, agents_router, assets_router),
    migrations=(Path(__file__).parent / "migrations" / "versions",),
    sweeps=(_advance_threads,),
    providers=BUILT_IN_MODEL_PROVIDERS,
)

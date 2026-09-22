"""Explicit composition of the implemented foundation; no package scanning."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import MetaData, Table

from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import Base
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow, WorkspaceRow


@dataclass(frozen=True)
class Distribution:
    name: str
    tables: tuple[type[Base], ...]
    migrations: tuple[Path, ...]
    routers: tuple[APIRouter, ...] = ()

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
    tables=(OrganizationRow, WorkspaceRow, PrincipalRow, PasswordRow, GrantRow, AuditEventRow),
    migrations=(Path(__file__).parent / "migrations" / "versions",),
)

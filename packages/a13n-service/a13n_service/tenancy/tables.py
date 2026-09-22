"""Identity rows required by initial bootstrap."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class OrganizationRow(Stamped, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class WorkspaceRow(Stamped, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "key"),
        UniqueConstraint("organization_id", "id"),
        Index("ix_workspaces_key", "key"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    key: Mapped[str]
    name: Mapped[str]
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrincipalRow(Stamped, Base):
    __tablename__ = "principals"
    __table_args__ = (
        CheckConstraint("kind IN ('user', 'service_account')", name="kind"),
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        CheckConstraint("(kind = 'user') = (email IS NOT NULL)", name="user_email"),
        CheckConstraint("(kind = 'service_account') = (home_workspace_id IS NOT NULL)", name="account_home"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    kind: Mapped[str]
    name: Mapped[str]
    email: Mapped[str | None] = mapped_column(unique=True)
    home_workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    status: Mapped[str] = mapped_column(server_default="active")


class PasswordRow(Base):
    __tablename__ = "passwords"
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), primary_key=True)
    hash: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GrantRow(Base):
    __tablename__ = "grants"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("uq_grants_scope", "principal_id", "organization_id", text("COALESCE(workspace_id, '')"), unique=True),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    role: Mapped[str]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

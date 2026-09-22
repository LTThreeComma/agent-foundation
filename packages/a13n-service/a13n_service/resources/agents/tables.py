"""Agent identity and immutable, tenant-owned revisions."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class AgentRow(Stamped, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "key"),
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["id", "default_revision_id"],
            ["agent_revisions.agent_id", "agent_revisions.id"],
            name="fk_agents_default_revision",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("source IN ('custom', 'builtin')", name="source"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    default_revision_id: Mapped[str | None] = mapped_column(String(72))
    labels: Mapped[dict] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(default="custom")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class AgentRevisionRow(Base):
    __tablename__ = "agent_revisions"
    __table_args__ = (
        UniqueConstraint("agent_id", "number"),
        UniqueConstraint("agent_id", "id"),
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        CheckConstraint("number > 0", name="number"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    agent_id: Mapped[str]
    number: Mapped[int]
    config: Mapped[dict] = mapped_column(JSONB)
    digest: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None]
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

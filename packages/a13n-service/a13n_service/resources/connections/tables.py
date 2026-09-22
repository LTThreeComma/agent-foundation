"""Live workspace Connections; credential envelopes never leave resource services."""

from typing import Literal

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class ConnectionRow(Stamped, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        CheckConstraint("auth IN ('none', 'bearer', 'headers')", name="auth"),
        CheckConstraint("auth <> 'none' OR credential IS NULL", name="no_anonymous_credential"),
        Index("ix_connections_workspace_id_id", "workspace_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    auth: Mapped[Literal["none", "bearer", "headers"]] = mapped_column(String)
    credential: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

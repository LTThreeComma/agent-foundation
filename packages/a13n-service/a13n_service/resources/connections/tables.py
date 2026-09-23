"""Live workspace Connections; credential envelopes never leave resource services."""

from datetime import datetime
from typing import Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class ConnectionRow(Stamped, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        CheckConstraint("auth IN ('none', 'bearer', 'headers', 'oauth')", name="auth"),
        CheckConstraint("auth <> 'none' OR credential IS NULL", name="no_anonymous_credential"),
        Index("ix_connections_workspace_id_id", "workspace_id", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    auth: Mapped[Literal["none", "bearer", "headers", "oauth"]] = mapped_column(String)
    credential: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class ConnectionAuthorizationRow(Stamped, Base):
    __tablename__ = "connection_authorizations"
    __table_args__ = (
        UniqueConstraint("connection_id", "principal_id"),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "connection_id"],
            ["connections.organization_id", "connections.workspace_id", "connections.id"],
        ),
        CheckConstraint("status IN ('pending', 'active', 'revoked', 'reauthorization_required')", name="status"),
        CheckConstraint("generation >= 0", name="generation"),
        CheckConstraint("operation_kind IN ('exchange', 'refresh')", name="operation_kind"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_kind IS NULL) AND "
            "(operation_id IS NULL) = (operation_deadline IS NULL)",
            name="operation_fields",
        ),
        CheckConstraint(
            "status NOT IN ('revoked', 'reauthorization_required') OR credential IS NULL", name="inactive_credential"
        ),
        Index("ix_connection_authorizations_deadline", "operation_deadline", "id"),
        Index("ix_connection_authorizations_expiry", "expires_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    connection_id: Mapped[str]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    status: Mapped[Literal["pending", "active", "revoked", "reauthorization_required"]] = mapped_column(String)
    credential: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    operation_id: Mapped[str | None] = mapped_column(String(72))
    operation_kind: Mapped[Literal["exchange", "refresh"] | None] = mapped_column(String)
    operation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_state_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    redirect_uri: Mapped[str | None]
    return_uri: Mapped[str | None]
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

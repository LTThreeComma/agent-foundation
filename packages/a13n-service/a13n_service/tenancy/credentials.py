"""Hashed bearer keys and expiring login credentials."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class TokenRow(Base):
    __tablename__ = "tokens"
    __table_args__ = (CheckConstraint("kind IN ('session','password_reset','email_change')", name="kind"),)
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), index=True)
    kind: Mapped[str]
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    data: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKeyRow(Stamped, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"), index=True)
    name: Mapped[str]
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

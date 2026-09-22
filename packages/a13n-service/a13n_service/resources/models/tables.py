"""Typed model/provider ownership; credentials remain encrypted and write-only."""

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class ModelProviderRow(Stamped, Base):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    credential: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class ModelRow(Stamped, Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("provider_id", "key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["organization_id", "provider_id"], ["model_providers.organization_id", "model_providers.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    provider_id: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    pricing: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

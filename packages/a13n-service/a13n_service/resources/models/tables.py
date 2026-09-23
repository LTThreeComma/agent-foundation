"""Models belong to one model provider; a shared provider may serve shared or workspace models."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.resources.providers.tables import provider_in_scope


class ModelRow(Stamped, Base):
    __tablename__ = "models"
    KIND: ClassVar[str] = "model"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("provider_id", "key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["organization_id", "provider_id"], ["model_providers.organization_id", "model_providers.id"]
        ),
        rules(identity_guarded("models"), *provider_in_scope("models", "provider_id", "model_providers")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    provider_id: Mapped[str]
    key: Mapped[str]
    name: Mapped[str]
    description: Mapped[str] = mapped_column(server_default="")
    config: Mapped[dict] = mapped_column(JSONB)
    pricing: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

"""An Asset is immutable upload content with independently retained retirement."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class AssetRow(Stamped, Base):
    __tablename__ = "assets"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "content_ref"),
        Index("ix_assets_workspace_created", "workspace_id", "created_at", "id"),
        CheckConstraint("size >= 0", name="size"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    content_type: Mapped[str]
    size: Mapped[int] = mapped_column(BigInteger)
    digest: Mapped[str]
    content_ref: Mapped[str]
    source: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

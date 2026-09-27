"""Workspace secrets, encrypted and bound to their immutable identity."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, immutable_columns, rules


class SecretRow(Stamped, Base):
    __tablename__ = "secrets"
    KIND: ClassVar[str] = "secret"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        UniqueConstraint("workspace_id", "key"),
        rules(identity_guarded("secrets"), immutable_columns("secrets", "key")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    key: Mapped[str]
    # An encryption envelope whose authenticated data is this row's identity.
    ciphertext: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

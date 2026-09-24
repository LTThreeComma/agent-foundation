"""Secret values, encrypted and bound to their own row; `principal_id` makes one private to its owner."""

from typing import ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class SecretRow(Stamped, Base):
    __tablename__ = "secrets"
    KIND: ClassVar[str] = "secret"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        # A key names at most one workspace secret and one private secret per principal.
        Index("uq_secrets_owner_key", "workspace_id", text("COALESCE(principal_id, '')"), "key", unique=True),
        rules(identity_guarded("secrets")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    key: Mapped[str]
    # An encryption envelope whose authenticated data is this row's identity.
    ciphertext: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

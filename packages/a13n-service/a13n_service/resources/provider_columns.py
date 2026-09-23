"""The shared shape of the four provider resource tables (model, web, connector, environment).

Each kind keeps its own table so the resources that use it hold typed foreign keys; only the columns and
their scope rules are shared. `workspace_id IS NULL` shares a provider with every workspace of the org.
"""

from typing import Any, ClassVar

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from a13n_service.infra.db import Stamped, identity_guarded, rules


class ProviderColumns(Stamped):
    __tablename__: ClassVar[str]

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    # Selects the registered provider definition whose schemas validate `config` and `credential`.
    type: Mapped[str]
    name: Mapped[str]
    config: Mapped[dict] = mapped_column(JSONB)
    # Encrypted, write-only envelope bound to this row.
    credential: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))

    @declared_attr.directive
    @classmethod
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            UniqueConstraint("organization_id", "id"),
            ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
            rules(identity_guarded(cls.__tablename__)),
        )

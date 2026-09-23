"""Environment instances and thread mounts.

An instance's `status` names its outstanding lifecycle operation; `operation_id` and `generation` fence
completions, and the `lease_*` columns are the claim of whoever is dispatching that operation right now.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules
from a13n_service.resources.providers.tables import provider_in_scope

STATUSES = ("creating", "starting", "ready", "stopping", "stopped", "deleting", "deleted")
OPERATIONS = "('creating', 'starting', 'stopping', 'deleting')"


class EnvironmentRow(Stamped, Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["organization_id", "provider_id"], ["environment_providers.organization_id", "environment_providers.id"]
        ),
        ForeignKeyConstraint(
            ["workspace_id", "template_id"], ["environment_templates.workspace_id", "environment_templates.id"]
        ),
        CheckConstraint(f"status IN {STATUSES}", name="status"),
        CheckConstraint(
            "status NOT IN ('ready', 'starting', 'stopping', 'stopped') OR handle IS NOT NULL", name="handle"
        ),
        # The status names the outstanding operation; no other state carries one.
        CheckConstraint(f"(status IN {OPERATIONS}) = (operation_id IS NOT NULL)", name="operation"),
        CheckConstraint("(operation_id IS NULL) = (operation_started_at IS NULL)", name="operation_started"),
        CheckConstraint("operation_deadline IS NULL OR operation_id IS NOT NULL", name="operation_deadline"),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_token_hash IS NULL) AND (lease_owner IS NULL) = (lease_expires_at IS NULL)"
            " AND (lease_owner IS NULL OR operation_id IS NOT NULL)",
            name="lease",
        ),
        # Registered devices are connect-only: never created, started, stopped or destroyed by the Service.
        CheckConstraint("template_id IS NOT NULL OR status IN ('ready', 'deleted')", name="connect_only"),
        CheckConstraint("(template_id IS NULL) = (device_id IS NOT NULL)", name="device"),
        Index(
            "uq_environments_operation", "operation_id", unique=True, postgresql_where=text("operation_id IS NOT NULL")
        ),
        Index("ix_environments_operations", "updated_at", postgresql_where=text(f"status IN {OPERATIONS}")),
        Index(
            "ix_environments_idle",
            "last_used_at",
            postgresql_where=text("template_id IS NOT NULL AND status IN ('ready', 'stopped')"),
        ),
        rules(
            identity_guarded("environments"),
            *provider_in_scope("environments", "provider_id", "environment_providers"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    provider_id: Mapped[str]
    # The non-secret provider type and backend locator that give `handle` meaning, frozen before the first
    # dispatch; NULL means no external operation was ever claimed for this instance.
    provider_identity: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    # NULL only for registered external devices, which are connect-only.
    template_id: Mapped[str | None]
    # A registered device's native identity, which its handle state names; the tombstone keeps it.
    device_id: Mapped[str | None]
    # Private devices belong to one principal; workspace-managed sandboxes have no owner.
    owner_principal_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    name: Mapped[str]
    status: Mapped[str]
    # The recipe the instance was built from and the provider's portable state for reaching it.
    handle: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    generation: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    operation_id: Mapped[str | None]
    operation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The bound of the provider call currently claimed.
    operation_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None]
    lease_token_hash: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class ThreadEnvironmentRow(Base):
    """A thread's desired mount; acceptance copies the set into `runs.environment_mounts`."""

    __tablename__ = "thread_environments"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "name"),
        UniqueConstraint("thread_id", "environment_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "environment_id"], ["environments.workspace_id", "environments.id"]),
        Index("ix_thread_environments_environment", "environment_id"),
        rules(
            # Mount edits are visible thread changes, so they advance the thread version like inbox edits.
            "CREATE TRIGGER touch_threads_on_mount_insert AFTER INSERT ON thread_environments"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_mount_update AFTER UPDATE ON thread_environments"
            " REFERENCING NEW TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
            "CREATE TRIGGER touch_threads_on_mount_delete AFTER DELETE ON thread_environments"
            " REFERENCING OLD TABLE AS changed FOR EACH STATEMENT EXECUTE FUNCTION touch_threads()",
        ),
    )
    thread_id: Mapped[str] = mapped_column(String(72))
    environment_id: Mapped[str] = mapped_column(String(72))
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    name: Mapped[str]
    working_directory: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

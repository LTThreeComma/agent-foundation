"""Execution ownership, immutable input assignment and terminal selections."""

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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped


class SessionRow(Stamped, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    labels: Mapped[dict] = mapped_column(JSONB)
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))


class ThreadRow(Stamped, Base):
    __tablename__ = "threads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "session_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "session_id"], ["sessions.workspace_id", "sessions.id"]),
        ForeignKeyConstraint(
            ["id", "current_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_threads_current_run",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "head_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_threads_head_run",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "last_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_threads_last_run",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "origin_thread_id"],
            ["threads.workspace_id", "threads.session_id", "threads.id"],
            name="fk_threads_origin_thread",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "origin_run_id"],
            ["runs.workspace_id", "runs.session_id", "runs.id"],
            name="fk_threads_origin_run",
            use_alter=True,
        ),
        CheckConstraint("origin IN ('new','fork','child')", name="origin"),
        CheckConstraint(
            "(origin = 'new' AND origin_thread_id IS NULL AND origin_run_id IS NULL AND origin_tool_call_id IS NULL) OR (origin = 'fork' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL AND origin_tool_call_id IS NULL) OR (origin = 'child' AND origin_thread_id IS NOT NULL AND origin_run_id IS NOT NULL AND origin_tool_call_id IS NOT NULL)",
            name="origin_links",
        ),
        Index(
            "uq_threads_child_origin",
            "origin_run_id",
            "origin_tool_call_id",
            unique=True,
            postgresql_where=text("origin = 'child'"),
        ),
        Index(
            "ix_threads_idle",
            "updated_at",
            "id",
            postgresql_where=text("current_run_id IS NULL AND archived_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    session_id: Mapped[str]
    origin: Mapped[str]
    origin_thread_id: Mapped[str | None]
    origin_run_id: Mapped[str | None]
    origin_tool_call_id: Mapped[str | None]
    current_run_id: Mapped[str | None] = mapped_column(String(72))
    head_run_id: Mapped[str | None] = mapped_column(String(72))
    last_run_id: Mapped[str | None] = mapped_column(String(72))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[dict] = mapped_column(JSONB)


class InboxEntryRow(Base):
    __tablename__ = "inbox_entries"
    __table_args__ = (
        UniqueConstraint("thread_id", "position", deferrable=True, initially="IMMEDIATE"),
        UniqueConstraint("thread_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        ForeignKeyConstraint(["agent_id", "agent_revision_id"], ["agent_revisions.agent_id", "agent_revisions.id"]),
        ForeignKeyConstraint(
            ["thread_id", "assigned_run_id"],
            ["runs.thread_id", "runs.id"],
            name="fk_inbox_assigned_run",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_inbox_request",
            "workspace_id",
            "principal_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key IS NOT NULL"),
        ),
        Index("uq_inbox_child_result", "child_run_id", unique=True, postgresql_where=text("kind = 'child_result'")),
        Index("ix_inbox_pending", "thread_id", "position", postgresql_where=text("status = 'pending'")),
        CheckConstraint("kind IN ('message','feedback','child_result')", name="kind"),
        CheckConstraint("delivery IN ('steer','next_run')", name="delivery"),
        CheckConstraint("status IN ('pending','assigned','consumed','failed','withdrawn')", name="status"),
        CheckConstraint("position > 0", name="position"),
        CheckConstraint("(payload IS NULL) <> (payload_ref IS NULL)", name="payload"),
        CheckConstraint("(kind = 'message') = (agent_id IS NOT NULL)", name="message_agent"),
        CheckConstraint("(kind = 'feedback') = (waiting_run_id IS NOT NULL)", name="feedback_target"),
        CheckConstraint(
            "(kind = 'child_result') = (child_run_id IS NOT NULL AND origin_run_id IS NOT NULL)", name="child_target"
        ),
        CheckConstraint("status NOT IN ('assigned','consumed') OR assigned_run_id IS NOT NULL", name="assignment"),
        CheckConstraint("(status = 'consumed') = (incorporated_checkpoint_seq IS NOT NULL)", name="receipt"),
        CheckConstraint("status <> 'pending' OR assigned_run_id IS NULL", name="pending"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    thread_id: Mapped[str]
    kind: Mapped[str]
    delivery: Mapped[str]
    position: Mapped[int] = mapped_column(BigInteger)
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    payload_ref: Mapped[str | None]
    agent_id: Mapped[str | None]
    agent_revision_id: Mapped[str | None]
    options: Mapped[dict] = mapped_column(JSONB)
    waiting_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", use_alter=True))
    child_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", use_alter=True))
    origin_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", use_alter=True))
    request_key: Mapped[str | None]
    request_digest: Mapped[str | None]
    request_kind: Mapped[str | None]
    request_target: Mapped[str | None]
    status: Mapped[str]
    assigned_run_id: Mapped[str | None]
    incorporated_checkpoint_seq: Mapped[int | None] = mapped_column(BigInteger)
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunRow(Stamped, Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("thread_id", "id"),
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("workspace_id", "session_id", "id"),
        UniqueConstraint("source_entry_id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "thread_id"], ["threads.workspace_id", "threads.session_id", "threads.id"]
        ),
        ForeignKeyConstraint(["workspace_id", "agent_id"], ["agents.workspace_id", "agents.id"]),
        ForeignKeyConstraint(["agent_id", "agent_revision_id"], ["agent_revisions.agent_id", "agent_revisions.id"]),
        ForeignKeyConstraint(
            ["thread_id", "source_entry_id"],
            ["inbox_entries.thread_id", "inbox_entries.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "session_id", "parent_run_id"], ["runs.workspace_id", "runs.session_id", "runs.id"]
        ),
        ForeignKeyConstraint(
            ["id", "current_attempt_id"],
            ["run_attempts.run_id", "run_attempts.id"],
            name="fk_runs_current_attempt",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_runs_active_thread", "thread_id", unique=True, postgresql_where=text("status IN ('accepted','running')")
        ),
        Index("ix_runs_due", "available_at", postgresql_where=text("status = 'accepted'")),
        CheckConstraint("revision_selection IN ('pinned','default','inherited')", name="revision_selection"),
        CheckConstraint("trigger IN ('input','queued','feedback','child_result','spawned')", name="trigger"),
        CheckConstraint("lineage IN ('root','continue','fork')", name="lineage"),
        CheckConstraint("status IN ('accepted','running','waiting','completed','failed','cancelled')", name="status"),
        CheckConstraint("wait_reason IN ('approval','client_tool','user_input','multiple')", name="wait_reason"),
        CheckConstraint("(lineage = 'root') = (parent_run_id IS NULL)", name="parent"),
        CheckConstraint(
            "(status IN ('waiting','completed','failed','cancelled')) = (sealed_at IS NOT NULL)", name="sealed"
        ),
        CheckConstraint("(status IN ('failed','cancelled')) = (failure IS NOT NULL)", name="failure"),
        CheckConstraint("(status IN ('waiting','completed')) = (sealed_checkpoint IS NOT NULL)", name="checkpoint"),
        CheckConstraint("(status IN ('waiting','completed')) = (sealed_display IS NOT NULL)", name="display"),
        CheckConstraint("(status = 'waiting') = (wait_reason IS NOT NULL AND pending IS NOT NULL)", name="waiting"),
        CheckConstraint("(status = 'running') = (current_attempt_id IS NOT NULL)", name="attempt"),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    session_id: Mapped[str]
    thread_id: Mapped[str]
    agent_id: Mapped[str]
    agent_revision_id: Mapped[str]
    revision_selection: Mapped[str]
    principal_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    authority: Mapped[dict] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB)
    environment_mounts: Mapped[list] = mapped_column(JSONB)
    source_entry_id: Mapped[str]
    trigger: Mapped[str]
    lineage: Mapped[str]
    parent_run_id: Mapped[str | None]
    status: Mapped[str]
    wait_reason: Mapped[str | None]
    pending: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_attempt_id: Mapped[str | None] = mapped_column(String(72))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int]
    max_usage: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    sealed_checkpoint: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    sealed_display: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    output_ref: Mapped[str | None]
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    usage_at_seal: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    labels: Mapped[dict] = mapped_column(JSONB)
    lifecycle_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptRow(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "number"),
        UniqueConstraint("run_id", "id"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["run_id", "replaces_attempt_id"], ["run_attempts.run_id", "run_attempts.id"]),
        Index("uq_attempts_live_run", "run_id", unique=True, postgresql_where=text("status IN ('leased','running')")),
        Index("ix_attempts_expiry", "lease_expires_at", postgresql_where=text("status IN ('leased','running')")),
        CheckConstraint("status IN ('leased','running','succeeded','yielded','failed','cancelled')", name="status"),
        CheckConstraint("start_reason IN ('initial','recovery','handoff')", name="start_reason"),
        CheckConstraint(
            "(status IN ('succeeded','yielded','failed','cancelled')) = (finished_at IS NOT NULL)", name="finished"
        ),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    run_id: Mapped[str]
    number: Mapped[int]
    status: Mapped[str]
    start_reason: Mapped[str]
    replaces_attempt_id: Mapped[str | None]
    worker_id: Mapped[str]
    worker_build: Mapped[str]
    harness_run_id: Mapped[str | None]
    lease_token_hash: Mapped[str]
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yield_reason: Mapped[str | None]
    failure: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    lifecycle_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

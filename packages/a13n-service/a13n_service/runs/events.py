"""Workspace commit-ordered lifecycle facts, flushed after all domain row locks."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.tables import AttemptRow, RunRow


class EventCursorRow(Base):
    __tablename__ = "event_cursors"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
    )
    workspace_id: Mapped[str] = mapped_column(primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    last_seq: Mapped[int] = mapped_column(BigInteger)
    retained_from: Mapped[int] = mapped_column(BigInteger)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "seq"),
        UniqueConstraint("entity_kind", "entity_id", "entity_seq"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "session_id"], ["sessions.workspace_id", "sessions.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["run_id", "run_attempt_id"], ["run_attempts.run_id", "run_attempts.id"]),
        CheckConstraint(
            "kind IN ('run.accepted','run.running','run.waiting','run.completed','run.failed','run.cancelled','run_attempt.leased','run_attempt.running','run_attempt.succeeded','run_attempt.yielded','run_attempt.failed','run_attempt.cancelled')",
            name="kind",
        ),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    seq: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str]
    entity_kind: Mapped[str]
    entity_id: Mapped[str]
    entity_seq: Mapped[int] = mapped_column(BigInteger)
    mutation_id: Mapped[str]
    session_id: Mapped[str]
    thread_id: Mapped[str]
    run_id: Mapped[str]
    run_attempt_id: Mapped[str | None]
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    payload: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def stage(run: RunRow, *, attempt: AttemptRow | None = None) -> EventRow:
    entity = attempt if attempt is not None else run
    kind: Literal["run", "run_attempt"] = "run_attempt" if attempt is not None else "run"
    entity.lifecycle_seq += 1
    return EventRow(
        id=new_object_id("evt"),
        organization_id=run.organization_id,
        workspace_id=run.workspace_id,
        kind=f"{kind}.{entity.status}",
        entity_kind=kind,
        entity_id=entity.id,
        entity_seq=entity.lifecycle_seq,
        session_id=run.session_id,
        thread_id=run.thread_id,
        run_id=run.id,
        run_attempt_id=attempt.id if attempt is not None else None,
        actor_id=run.principal_id,
        payload={"status": entity.status},
    )


async def flush(session: AsyncSession, events: Sequence[EventRow]) -> None:
    """Last SQL phase: no domain lock or external I/O may follow before commit."""
    if not events:
        return
    organization_id, workspace_id = events[0].organization_id, events[0].workspace_id
    if any(event.organization_id != organization_id or event.workspace_id != workspace_id for event in events):
        raise ValueError("One event batch belongs to one workspace")
    await session.flush()
    await session.execute(
        insert(EventCursorRow)
        .values(
            organization_id=organization_id,
            workspace_id=workspace_id,
            last_seq=0,
            retained_from=1,
        )
        .on_conflict_do_nothing(index_elements=["workspace_id"])
    )
    cursor = (
        await session.execute(
            select(EventCursorRow).where(EventCursorRow.workspace_id == workspace_id).with_for_update()
        )
    ).scalar_one()
    mutation_id = new_object_id("mut")
    for event in events:
        cursor.last_seq += 1
        event.seq = cursor.last_seq
        event.mutation_id = mutation_id
        session.add(event)
    await session.flush()

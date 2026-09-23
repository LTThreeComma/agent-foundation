"""A thread's queued input: append under capacity, pending-only edits, steer assignment and disposition.

Every function here runs inside the caller's transaction, which already holds the thread lock. Input usage
is the count and bytes of pending plus assigned entries; `occupancy` is the only place that counts it.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.runs.schemas import EntryUpdate, Failure, Message, RunOptions, canonical_json
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.tenancy.authorize import ExecutionAuthority

type RequestKind = Literal["thread", "message", "fork"]

OUTSTANDING = ("pending", "assigned")


@dataclass(frozen=True, slots=True)
class InboxLimits:
    count: int
    bytes: int


@dataclass(frozen=True, slots=True)
class Request:
    """Replay evidence for an idempotent message command, stored on the entry it creates."""

    key: str
    kind: RequestKind
    target: str
    digest: str

    @classmethod
    def of(cls, key: str, kind: RequestKind, target: str, body: BaseModel) -> "Request":
        digest = hashlib.sha256(canonical_json([kind, target, body.model_dump(mode="json")])).hexdigest()
        return cls(key=key, kind=kind, target=target, digest=digest)


async def occupancy(session: AsyncSession, thread_id: str) -> tuple[int, int]:
    count, size = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(InboxEntryRow.size), 0)).where(
                InboxEntryRow.thread_id == thread_id, InboxEntryRow.status.in_(OUTSTANDING)
            )
        )
    ).one()
    return int(count), int(size)


async def require_capacity(
    session: AsyncSession, thread_id: str, limits: InboxLimits, *, adding: int, growth: int
) -> None:
    count, size = await occupancy(session, thread_id)
    if count + adding > limits.count or size + growth > limits.bytes:
        raise ServiceError(
            "rate_limited",
            "Thread inbox is full",
            {"retry_after": 1, "count": count, "bytes": size, "limit_count": limits.count, "limit_bytes": limits.bytes},
        )


async def find_request(session: AsyncSession, workspace_id: str, principal_id: str, key: str) -> InboxEntryRow | None:
    return await session.scalar(
        select(InboxEntryRow).where(
            InboxEntryRow.workspace_id == workspace_id,
            InboxEntryRow.principal_id == principal_id,
            InboxEntryRow.request_key == key,
        )
    )


def check_replay(entry: InboxEntryRow, request: Request) -> None:
    """A reused key must name the same operation, target and canonical body; pending edits never change it."""
    if (entry.request_kind, entry.request_target, entry.request_digest) != (
        request.kind,
        request.target,
        request.digest,
    ):
        raise conflict("request", request.key, "idempotency_key_reused")


async def _next_position(session: AsyncSession, thread_id: str) -> int:
    highest = await session.scalar(select(func.max(InboxEntryRow.position)).where(InboxEntryRow.thread_id == thread_id))
    return (highest or 0) + 1


async def append_message(
    session: AsyncSession,
    thread: ThreadRow,
    message: Message,
    *,
    principal_id: str,
    authority: ExecutionAuthority,
    request: Request | None,
    limits: InboxLimits,
) -> InboxEntryRow:
    payload = message.payload.model_dump(mode="json")
    size = len(canonical_json(payload))
    await require_capacity(session, thread.id, limits, adding=1, growth=size)
    entry = InboxEntryRow(
        id=new_object_id("inb"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        kind="message",
        delivery=message.delivery,
        position=await _next_position(session, thread.id),
        principal_id=principal_id,
        authority=authority.model_dump(mode="json"),
        payload=payload,
        size=size,
        agent_id=message.agent_id,
        agent_revision_id=message.agent_revision_id,
        options=message.options.model_dump(mode="json"),
        request_key=request.key if request else None,
        request_digest=request.digest if request else None,
        request_kind=request.kind if request else None,
        request_target=request.target if request else None,
        status="pending",
    )
    session.add(entry)
    await session.flush()
    return entry


async def append_child_result(
    session: AsyncSession, thread: ThreadRow, child_run: RunRow, origin_run: RunRow, *, limits: InboxLimits
) -> InboxEntryRow | None:
    """One result entry per sealed child run; None when the inbox cannot take it now (delivery retries)."""
    payload = {
        "child_run_id": child_run.id,
        "status": child_run.status,
        "output": child_run.output,
        "failure": child_run.failure,
    }
    size = len(canonical_json(payload))
    count, used = await occupancy(session, thread.id)
    if count + 1 > limits.count or used + size > limits.bytes:
        return None
    entry = InboxEntryRow(
        id=new_object_id("inb"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        kind="child_result",
        delivery="steer",
        position=await _next_position(session, thread.id),
        principal_id=origin_run.principal_id,
        authority=origin_run.authority,
        payload=payload,
        size=size,
        options={},
        child_run_id=child_run.id,
        origin_run_id=origin_run.id,
        status="pending",
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_entry(session: AsyncSession, thread: ThreadRow, entry_id: str, *, lock: bool = False) -> InboxEntryRow:
    query = select(InboxEntryRow).where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.id == entry_id)
    entry = await session.scalar(query.with_for_update() if lock else query)
    if entry is None:
        raise not_found("inbox_entry", entry_id)
    return entry


def _require_pending(entry: InboxEntryRow) -> None:
    if entry.status != "pending":
        raise conflict("inbox_entry", entry.id, f"entry_{entry.status}")


async def edit_entry(
    session: AsyncSession, thread: ThreadRow, entry_id: str, update_: EntryUpdate, *, limits: InboxLimits
) -> InboxEntryRow:
    entry = await get_entry(session, thread, entry_id, lock=True)
    _require_pending(entry)
    if entry.kind != "message":
        raise conflict("inbox_entry", entry.id, "not_a_message")
    if update_.payload is not None:
        payload = update_.payload.model_dump(mode="json")
        size = len(canonical_json(payload))
        await require_capacity(session, thread.id, limits, adding=0, growth=size - entry.size)
        entry.payload, entry.size = payload, size
    if update_.delivery is not None:
        entry.delivery = update_.delivery
    if "agent_revision_id" in update_.model_fields_set:
        entry.agent_revision_id = update_.agent_revision_id
    if update_.options is not None:
        entry.options = update_.options.model_dump(mode="json")
    await session.flush()
    return entry


async def withdraw_entry(session: AsyncSession, thread: ThreadRow, entry_id: str, *, at: datetime) -> InboxEntryRow:
    """Withdrawal keeps a tombstone, so the request key can never be reused for different input."""
    entry = await get_entry(session, thread, entry_id, lock=True)
    _require_pending(entry)
    entry.status, entry.finished_at = "withdrawn", at
    await session.flush()
    return entry


async def withdraw_pending(session: AsyncSession, thread: ThreadRow, *, at: datetime) -> None:
    await session.execute(
        update(InboxEntryRow)
        .where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.status == "pending")
        .values(status="withdrawn", finished_at=at)
    )


async def reorder(session: AsyncSession, thread: ThreadRow, entry_ids: Sequence[str]) -> None:
    """The order names exactly the pending entries; they keep their set of positions in the new order."""
    pending = (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.thread_id == thread.id, InboxEntryRow.status == "pending")
            .order_by(InboxEntryRow.position)
            .with_for_update()
        )
    ).all()
    if {entry.id for entry in pending} != set(entry_ids) or len(pending) != len(entry_ids):
        raise invalid("entry_ids", "must list exactly the pending entries")
    positions = [entry.position for entry in pending]
    by_id = {entry.id: entry for entry in pending}
    await session.execute(text("SET CONSTRAINTS uq_inbox_entries_thread_id_position DEFERRED"))
    for position, entry_id in zip(positions, entry_ids, strict=True):
        by_id[entry_id].position = position
    await session.flush()


async def pending_entries(session: AsyncSession, thread_id: str, *, limit: int) -> Sequence[InboxEntryRow]:
    return (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.thread_id == thread_id, InboxEntryRow.status == "pending")
            .order_by(InboxEntryRow.position)
            .limit(limit)
            .with_for_update()
        )
    ).all()


def fail(entry: InboxEntryRow, failure: Failure, *, at: datetime) -> None:
    entry.status, entry.failure, entry.finished_at = "failed", failure.model_dump(), at


def assign(entry: InboxEntryRow, run: RunRow) -> None:
    entry.status, entry.assigned_run_id = "assigned", run.id


def steers_into(entry: InboxEntryRow, run: RunRow, origin: RunRow | None) -> bool:
    """Compatibility is configuration, not identity: headers and principals take no part."""
    if entry.kind == "child_result":
        # The origin must be this run or already in its history; a later failure never revives it.
        return origin is not None and (origin.id == run.id or origin.status in {"completed", "waiting"})
    return (
        entry.delivery == "steer"
        and entry.agent_id == run.agent_id
        and entry.agent_revision_id in {None, run.agent_revision_id}
        and RunOptions.model_validate(entry.options) == RunOptions.model_validate(run_options(run))
    )


def run_options(run: RunRow) -> dict:
    """The message-chosen part of a run's frozen options, without the thread's headers."""
    return {key: value for key, value in run.options.items() if key != "mcp_headers"}


async def assign_steers(
    session: AsyncSession, thread: ThreadRow, run: RunRow, *, max_count: int, max_bytes: int, scan: int
) -> list[InboxEntryRow]:
    """A bounded FIFO batch of compatible pending entries for the next model request of `run`."""
    candidates = await pending_entries(session, thread.id, limit=scan)
    origins = await _origins(session, candidates)
    chosen: list[InboxEntryRow] = []
    size = 0
    for entry in candidates:
        if len(chosen) == max_count or size + entry.size > max_bytes:
            break
        if steers_into(entry, run, origins.get(entry.origin_run_id or "")):
            assign(entry, run)
            chosen.append(entry)
            size += entry.size
    await session.flush()
    return chosen


async def _origins(session: AsyncSession, entries: Sequence[InboxEntryRow]) -> dict[str, RunRow]:
    ids = {entry.origin_run_id for entry in entries if entry.origin_run_id is not None}
    if not ids:
        return {}
    return {run.id: run for run in await session.scalars(select(RunRow).where(RunRow.id.in_(ids)))}


async def assigned_entries(session: AsyncSession, run_id: str) -> Sequence[InboxEntryRow]:
    return (
        await session.scalars(
            select(InboxEntryRow)
            .where(InboxEntryRow.assigned_run_id == run_id, InboxEntryRow.status == "assigned")
            .order_by(InboxEntryRow.position)
        )
    ).all()


async def consume(
    session: AsyncSession, run_id: str, entry_ids: Sequence[str], *, checkpoint_seq: int, at: datetime
) -> None:
    if entry_ids:
        await session.execute(
            update(InboxEntryRow)
            .where(
                InboxEntryRow.assigned_run_id == run_id,
                InboxEntryRow.status == "assigned",
                InboxEntryRow.id.in_(entry_ids),
            )
            .values(status="consumed", incorporated_checkpoint_seq=checkpoint_seq, finished_at=at)
        )


async def release_assigned(session: AsyncSession, run: RunRow, *, at: datetime) -> None:
    """Seal disposition: completed/waiting return unincorporated entries to pending; failure fails them.

    Consumed entries stay consumed either way: they record committed incorporation, not successful work.
    """
    if run.status in {"completed", "waiting"}:
        values = {"status": "pending", "assigned_run_id": None}
    else:
        values = {
            "status": "failed",
            "finished_at": at,
            "failure": Failure(code="run_ended", message=f"The assigned run was {run.status}").model_dump(),
        }
    await session.execute(
        update(InboxEntryRow)
        .where(InboxEntryRow.assigned_run_id == run.id, InboxEntryRow.status == "assigned")
        .values(**values)
    )

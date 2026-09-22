"""The one public input projection: canonical message content and truthful disposition."""

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.cursors import decode, encode
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schemas import EntryView, MessagePayload
from a13n_service.runs.tables import InboxEntryRow
from a13n_service.settings import MAX_INBOX_BYTES


def project(entry: InboxEntryRow) -> EntryView:
    return EntryView.model_validate(
        {
            "id": entry.id,
            "thread_id": entry.thread_id,
            "kind": entry.kind,
            "delivery": entry.delivery,
            "position": entry.position,
            "status": entry.status,
            "assigned_run_id": entry.assigned_run_id,
            "incorporated_checkpoint_seq": entry.incorporated_checkpoint_seq,
            "failure": entry.failure,
            "payload": MessagePayload.model_validate(entry.payload)
            if entry.kind == "message" and entry.payload is not None
            else None,
        }
    )


async def page(
    session: AsyncSession,
    *,
    thread_id: str,
    run_id: str | None,
    limit: int,
    cursor: str | None,
    max_bytes: int = 1048576,
) -> tuple[list[EntryView], str | None]:
    if not 1 <= limit <= 200:
        raise ServiceError("invalid_argument", "Input page limit must be between 1 and 200")
    owner, kind = (run_id, "run-input") if run_id is not None else (thread_id, "inbox")
    position = 0
    if cursor is not None:
        values = decode(cursor, kind, owner)
        if len(values) != 1 or type(values[0]) is not int or values[0] < 0:
            raise ServiceError("invalid_cursor", "Invalid input position")
        position = values[0]
    filters = [InboxEntryRow.thread_id == thread_id, InboxEntryRow.position > position]
    if run_id is not None:
        filters.append(InboxEntryRow.assigned_run_id == run_id)
    metadata = (
        await session.execute(
            select(
                InboxEntryRow.id,
                InboxEntryRow.position,
                func.coalesce(func.octet_length(cast(InboxEntryRow.payload, String)), 0),
            )
            .where(*filters)
            .order_by(InboxEntryRow.position)
            .limit(limit + 1)
        )
    ).all()
    # A legal large input occupies one page by itself; the normal page budget is not an input limit.
    selected: list[str] = []
    size = 0
    for identity, entry_position, byte_count in metadata[:limit]:
        if byte_count > MAX_INBOX_BYTES:
            raise ServiceError("payload_too_large", "Stored input exceeds the supported inbox capacity")
        if selected and size + byte_count > max_bytes:
            break
        selected.append(identity)
        size += byte_count
        position = entry_position
    rows = (
        (
            await session.scalars(
                select(InboxEntryRow).where(*filters, InboxEntryRow.id.in_(selected)).order_by(InboxEntryRow.position)
            )
        ).all()
        if selected
        else []
    )
    next_cursor = encode(kind, owner, position) if len(selected) < len(metadata) else None
    return [project(row) for row in rows], next_cursor

"""Bounded conditional object writes and exact-byte acknowledgement reconciliation."""

import asyncio

from a13n_service.infra.objects.local import LocalObjects, StoredObject
from a13n_service.runs.schemas import SnapshotRef


def object_key(organization_id: str, run_id: str, kind: str) -> str:
    if kind not in {"state", "display"}:
        raise ValueError("Unknown run snapshot kind")
    return f"orgs/{organization_id}/runs/{run_id}/{kind}.json"


def reference(stored: StoredObject, *, format: str, sequence: int, attempt_id: str) -> SnapshotRef:
    return SnapshotRef(
        digest=stored.digest,
        size=len(stored.content),
        format=format,
        sequence=sequence,
        attempt_id=attempt_id,
        version=stored.version,
    )


async def replace(
    objects: LocalObjects, key: str, content: bytes, *, expected: str | None, writer: int, timeout: float
) -> StoredObject:
    try:
        async with asyncio.timeout(timeout):
            return await objects.replace_snapshot(key, content, expected=expected, writer=writer)
    except (OSError, TimeoutError):
        async with asyncio.timeout(timeout):
            observed = await objects.read(key)
        if (
            observed is not None
            and observed.writer == writer
            and observed.content == content
            and observed.version != expected
        ):
            return observed
        raise

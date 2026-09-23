"""Run state and display objects, the fenced checkpoint commit and run-prefix cleanup.

Objects are immutable and digest-keyed under the run's prefix. Only the typed pointers on the run row make
them reachable, and only a transaction proving the worker lease moves those pointers, so a stale attempt's
late bytes are garbage, never state.
"""

import hashlib
from collections.abc import Sequence
from typing import Literal

from a13n_harness import HarnessState
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import conflict
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.runs import inbox
from a13n_service.runs.attempts import Lease, LeaseLost, lock_thread_lease
from a13n_service.runs.display import Display, StreamPosition
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Pending
from a13n_service.runs.tables import RunRow
from a13n_service.runs.usage import UsageReport, ingest

# Bumped only with an explicit migration or rejection plan for outstanding checkpoints.
FORMAT = 1

type ObjectKind = Literal["state", "display"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Pointer(_Frozen):
    """What `runs.checkpoint` and `runs.display` hold: the object's digest plus its commit identity."""

    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    format: int
    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)


class DisplayPointer(Pointer):
    position: StreamPosition


class Outcome(_Frozen):
    """A completed or waiting result, committed with the state that produced it so seal writes nothing."""

    status: Literal["completed", "waiting"]
    output: JsonValue = None
    pending: Pending | None = None
    # The Harness deferred requests the successor's resume answers; opaque to the Service.
    requests: JsonValue = None


class RunState(_Frozen):
    format: Literal[1] = FORMAT
    harness: HarnessState
    revision_digest: str
    options_digest: str
    seq: int = Field(ge=1)
    attempt: int = Field(ge=1)
    outcome: Outcome | None = None


def prefix(organization_id: str, run_id: str, kind: ObjectKind) -> str:
    return f"orgs/{organization_id}/runs/{run_id}/{kind}"


async def publish(objects: ObjectStore, organization_id: str, run_id: str, kind: ObjectKind, data: bytes) -> ObjectRef:
    """Write outside any session; the caller commits the reference only after the store acknowledged it."""
    digest = hashlib.sha256(data).hexdigest()
    return await objects.put(f"{prefix(organization_id, run_id, kind)}/{digest}", data, content_type="application/json")


def _ref(run: RunRow, kind: ObjectKind, pointer: Pointer) -> ObjectRef:
    return ObjectRef(
        key=f"{prefix(run.organization_id, run.id, kind)}/{pointer.digest}",
        digest=pointer.digest,
        size=pointer.size,
        content_type="application/json",
    )


def require_compatible(run: RunRow) -> None:
    """A run can continue only from a checkpoint this build understands."""
    if run.checkpoint is None or Pointer.model_validate(run.checkpoint).format != FORMAT:
        raise conflict("run", run.id, "checkpoint_incompatible")


async def load_state(objects: ObjectStore, run: RunRow) -> RunState | None:
    if run.checkpoint is None:
        return None
    require_compatible(run)
    pointer = Pointer.model_validate(run.checkpoint)
    return RunState.model_validate_json(await read(objects, _ref(run, "state", pointer)))


async def load_display(objects: ObjectStore, run: RunRow) -> Display | None:
    if run.display is None:
        return None
    pointer = DisplayPointer.model_validate(run.display)
    return Display.model_validate_json(await read(objects, _ref(run, "display", pointer)))


async def commit(
    runtime: Runtime,
    lease: Lease,
    *,
    previous: dict | None,
    state: Pointer,
    display: DisplayPointer,
    consumed: Sequence[str],
    usage: Sequence[UsageReport],
) -> None:
    """The checkpoint's durability point: pointers, consumption and usage move together or not at all.

    `previous` is the checkpoint pointer this attempt last committed (or found at start). Any other value
    means another writer moved it, which the lease predicate already rules out; it is checked anyway because
    consuming input against the wrong state would break at-most-once incorporation.
    """
    async with transaction(runtime.storage) as session:
        # Thread first: consuming entries fires the thread-version trigger, which updates the thread row.
        _, run, attempt, current = await lock_thread_lease(session, lease)
        if run.checkpoint != previous:
            raise LeaseLost()
        run.checkpoint = state.model_dump(mode="json")
        run.display = display.model_dump(mode="json")
        await inbox.consume(session, run.id, consumed, checkpoint_seq=state.seq, at=current)
        await ingest(session, run, attempt, usage)


async def clean(objects: ObjectStore, run: RunRow, *, limit: int = 1000) -> None:
    """Delete every state/display object of this run that its pointers do not name.

    Callers are the run's owners only: a worker after its own commit, a takeover before entering the
    Harness, and every seal after commit. Objects named by frozen pointers are never deleted.
    """
    pointers: tuple[tuple[ObjectKind, dict | None], ...] = (("state", run.checkpoint), ("display", run.display))
    keep = {_ref(run, kind, Pointer.model_validate(value)).key for kind, value in pointers if value is not None}
    for kind, _ in pointers:
        for key in await objects.keys(prefix(run.organization_id, run.id, kind), limit=limit):
            if key not in keep:
                await objects.delete(key)

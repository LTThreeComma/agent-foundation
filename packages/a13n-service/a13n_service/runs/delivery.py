"""SSE batches recheck SQL authority and reset only to an adequate durable view."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.infra.cursors import decode, encode
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.runs import views
from a13n_service.runs.streams import AttemptStream, Bounds
from a13n_service.tenancy.authorize import Principal


def position(cursor: str, run_id: str) -> tuple[int, int]:
    values = decode(cursor, "run-stream", run_id)
    if len(values) != 2:
        raise ServiceError("invalid_cursor", "Invalid run stream position")
    attempt, sequence = values
    if type(attempt) is not int or type(sequence) is not int or attempt < 1 or sequence < 0:
        raise ServiceError("invalid_cursor", "Invalid run stream position")
    return attempt, sequence


def frame(kind: str, data: dict, *, cursor: str | None = None) -> str:
    return (f"id: {cursor}\n" if cursor else "") + f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def control(kind: Literal["reset", "closed", "retry_later"], view: views.RunItems) -> str:
    return frame(
        kind,
        {
            "display_version": view.display_version,
            "cursor": view.cursor,
            "retry_after_ms": 1000 if kind == "retry_later" else 0,
        },
    )


async def stream(
    storage: Storage,
    objects: LocalObjects,
    redis: Redis,
    initial: views.RunItems,
    *,
    workspace_id: str,
    cursor: str | None,
    reauthenticate: Callable[[], Awaitable[Principal]],
    object_timeout: float,
    redis_timeout: float,
) -> AsyncIterator[str]:
    if initial.complete:
        yield control("closed", initial)
        return
    if cursor is None:
        yield control("reset", initial)
        return
    attempt, sequence = position(cursor, initial.run_id)
    replay = AttemptStream(redis, initial.run_id, attempt, Bounds(timeout=redis_timeout))
    last_view = initial
    while True:
        try:
            actor = await reauthenticate()
            view = await views.items(
                storage, objects, actor, workspace_id, initial.run_id, limit=1, cursor=None, timeout=object_timeout
            )
        except ServiceError as error:
            yield control(
                "closed" if error.code in {"forbidden", "unauthenticated", "not_found"} else "retry_later", last_view
            )
            return
        last_view = view
        if view.complete:
            yield control("closed", view)
            return
        if view.cursor is None or position(view.cursor, view.run_id)[0] != attempt:
            yield control("reset", view)
            return
        try:
            floor, rows = await replay.read(sequence)
        except (RedisError, TimeoutError):
            yield control("retry_later", view)
            return
        if floor < 0:
            yield control("reset" if floor == -2 else "retry_later", view)
            return
        if sequence < floor:
            # Trim occurs only after the display PUT. Refresh once after observing its newer floor.
            actor = await reauthenticate()
            view = await views.items(
                storage, objects, actor, workspace_id, initial.run_id, limit=1, cursor=None, timeout=object_timeout
            )
            adequate = (
                view.complete
                or view.cursor is None
                or position(view.cursor, view.run_id)[0] != attempt
                or position(view.cursor, view.run_id)[1] >= floor
            )
            yield control("closed" if view.complete else "reset" if adequate else "retry_later", view)
            return
        if rows:
            try:
                async with asyncio.timeout(2):
                    for sequence, event in rows:
                        yield frame(
                            "data",
                            {"attempt_number": attempt, "event_sequence": sequence, "event": event},
                            cursor=encode("run-stream", view.run_id, attempt, sequence),
                        )
            except TimeoutError:
                return
        else:
            await asyncio.sleep(0.2)

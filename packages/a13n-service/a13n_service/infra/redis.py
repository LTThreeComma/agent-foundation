"""Redis as a best-effort accelerator: rate limits, the claim wakeup marker and capped live streams.

Nothing here is authority. PostgreSQL decides ownership and durable state; every caller must behave
correctly, only slower, when Redis is unavailable or has lost data.
"""

import asyncio
import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError

WAKE_KEY = "a13n:wake"

_RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


async def rate_limit(client: Redis, identity: str, *, limit: int, window_seconds: int) -> None:
    key = "a13n:rate:" + hashlib.sha256(identity.encode()).hexdigest()
    try:
        count, remaining = await client.eval(_RATE_LIMIT, 1, key, window_seconds)
    except RedisError:
        raise ServiceError("unavailable", "Request limiter unavailable", {"dependency": "redis"}) from None
    if count > limit:
        raise ServiceError("rate_limited", "Too many requests", {"retry_after": max(1, remaining)})


async def wake(client: Redis, *, timeout: float) -> None:
    """Leave one "look now" marker for idle workers; a burst of wakes coalesces into one marker."""
    try:
        async with asyncio.timeout(timeout), client.pipeline(transaction=True) as pipe:
            pipe.rpush(WAKE_KEY, "1")
            pipe.ltrim(WAKE_KEY, -1, -1)
            await pipe.execute()
    except (RedisError, TimeoutError):
        pass  # The periodic scan finds the run within one interval.


async def wait_for_wake(client: Redis, *, timeout: float) -> None:
    """The claim loop's timer: return on a marker or after `timeout`; a Redis error sleeps the same interval."""
    try:
        await client.blpop([WAKE_KEY], timeout=timeout)
    except RedisError:
        await asyncio.sleep(timeout)


@dataclass(frozen=True, slots=True)
class StreamEntry:
    key: str
    id: str
    fields: dict[str, str]


async def append(client: Redis, key: str, fields: dict[str, str], *, max_length: int, ttl: int, timeout: float) -> bool:
    """Append under an approximate length cap; False means Redis dropped the entry."""
    try:
        async with asyncio.timeout(timeout), client.pipeline(transaction=False) as pipe:
            await pipe.xadd(key, fields, maxlen=max_length, approximate=True).expire(key, ttl).execute()  # type: ignore[arg-type]
        return True
    except (RedisError, TimeoutError):
        return False


async def read(client: Redis, cursors: dict[str, str], *, count: int, block_ms: int) -> list[StreamEntry]:
    """One blocking read over many stream keys; `unavailable` when Redis cannot answer."""
    try:
        # The client decodes responses, so keys, IDs and fields are strings.
        result: list[tuple[str, list[tuple[str, dict[str, str]]]]] = await client.xread(
            cursors,  # type: ignore[arg-type]
            count=count,
            block=block_ms,
        )
    except RedisError:
        raise ServiceError("unavailable", "Live stream is unavailable", {"dependency": "redis"}) from None
    return [
        StreamEntry(key=key, id=entry_id, fields=dict(values))
        for key, entries in result or ()
        for entry_id, values in entries
    ]


async def first_id(client: Redis, key: str) -> str | None:
    """The oldest retained entry; a cursor older than it has lost entries to trimming."""
    try:
        entries = await client.xrange(key, count=1)
    except RedisError:
        raise ServiceError("unavailable", "Live stream is unavailable", {"dependency": "redis"}) from None
    return str(entries[0][0]) if entries else None

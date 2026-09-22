"""Exactly bounded attempt-local Redis replay; trim requires a durable display prefix."""

import asyncio
import json
from dataclasses import dataclass

from pydantic import JsonValue
from redis.asyncio import Redis

from a13n_service.infra.errors import ServiceError
from a13n_service.runs import attempts
from a13n_service.runs.publisher import Publisher

_CREATE = """
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('XADD', KEYS[1], '0-1', 'data', '')
redis.call('XDEL', KEYS[1], '0-1')
redis.call('HSET', KEYS[2], 'floor', ARGV[1], 'last', ARGV[1], 'bytes', 0)
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
return 1
"""
_APPEND = """
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return -1 end
local size = string.len(ARGV[2])
if size > tonumber(ARGV[5]) then return -3 end
if tonumber(ARGV[1]) <= tonumber(redis.call('HGET', KEYS[2], 'last')) then return -4 end
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[3]) or
   tonumber(redis.call('HGET', KEYS[2], 'bytes')) + size > tonumber(ARGV[4]) then return -2 end
redis.call('XADD', KEYS[1], ARGV[1] .. '-0', 'data', ARGV[2])
redis.call('HINCRBY', KEYS[2], 'bytes', size)
redis.call('HSET', KEYS[2], 'last', ARGV[1])
return 1
"""
_TRIM = """
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return -1 end
local floor = tonumber(ARGV[1])
if floor <= tonumber(redis.call('HGET', KEYS[2], 'floor')) then return 0 end
local removed = redis.call('XRANGE', KEYS[1], '-', floor .. '-0')
local bytes = 0
for _, entry in ipairs(removed) do bytes = bytes + string.len(entry[2][2]) end
redis.call('XTRIM', KEYS[1], 'MINID', (floor + 1) .. '-0')
redis.call('HINCRBY', KEYS[2], 'bytes', -bytes)
redis.call('HSET', KEYS[2], 'floor', floor)
if floor > tonumber(redis.call('HGET', KEYS[2], 'last')) then redis.call('HSET', KEYS[2], 'last', floor) end
return #removed
"""
_READ = """
if redis.call('EXISTS', KEYS[1], KEYS[2]) ~= 2 then return {-1, {}} end
local floor = tonumber(redis.call('HGET', KEYS[2], 'floor'))
if tonumber(ARGV[1]) > tonumber(redis.call('HGET', KEYS[2], 'last')) then return {-2, {}} end
if tonumber(ARGV[1]) < floor then return {floor, {}} end
local rows = redis.call('XRANGE', KEYS[1], '(' .. ARGV[1] .. '-0', '+', 'COUNT', ARGV[2])
local selected = {}
local bytes = 0
for _, entry in ipairs(rows) do
  bytes = bytes + string.len(entry[2][2])
  if bytes > 1048576 then break end
  table.insert(selected, entry)
end
return {floor, selected}
"""


@dataclass(frozen=True)
class Bounds:
    count: int = 512
    bytes: int = 1048576
    entry_bytes: int = 65536
    ttl: int = 600
    timeout: float = 2


class AttemptStream:
    def __init__(self, redis: Redis, run_id: str, attempt: int, bounds: Bounds):
        self.redis, self.bounds = redis, bounds
        prefix = f"a13n:service:run:{{{run_id}:{attempt}}}"
        self.keys = (prefix + ":events", prefix + ":replay")

    async def create(self, floor: int) -> None:
        async with asyncio.timeout(self.bounds.timeout):
            await self.redis.eval(_CREATE, 2, *self.keys, floor, self.bounds.ttl)

    async def append(self, sequence: int, event: dict[str, JsonValue]) -> int:
        payload = json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(payload.encode()) > self.bounds.entry_bytes:
            raise ServiceError("payload_too_large", "Observation exceeds its replay entry limit")
        async with asyncio.timeout(self.bounds.timeout):
            result = await self.redis.eval(
                _APPEND, 2, *self.keys, sequence, payload, self.bounds.count, self.bounds.bytes, self.bounds.entry_bytes
            )
        if result in {-3, -4}:
            raise ServiceError("conflict", "Attempt stream rejected its observation sequence or size")
        return int(result)

    async def trim(self, floor: int) -> bool:
        async with asyncio.timeout(self.bounds.timeout):
            return await self.redis.eval(_TRIM, 2, *self.keys, floor) != -1

    async def read(self, sequence: int, *, count: int = 32) -> tuple[int, list[tuple[int, dict]]]:
        if not 1 <= count <= 64:
            raise ValueError("Replay batches contain at most 64 entries")
        async with asyncio.timeout(self.bounds.timeout):
            floor, rows = await self.redis.eval(_READ, 2, *self.keys, sequence, count)
        return int(floor), [(int(identity.split("-")[0]), json.loads(fields[1])) for identity, fields in rows]


class ObservationWriter:
    def __init__(self, stream: AttemptStream, publisher: Publisher, *, flush_seconds: float = 0.5):
        self.stream, self.publisher = stream, publisher
        self.flush_seconds = flush_seconds
        self._next_flush = 0.0

    def durable_sequence(self) -> int:
        if self.publisher.display_object is None:
            raise ServiceError("conflict", "Replay requires a durable display")
        from a13n_service.runs.display import Display

        display = Display.model_validate_json(self.publisher.display_object.content)
        return next(
            (
                segment.event_sequence
                for segment in display.segments
                if segment.attempt_id == self.publisher.claim.attempt_id
            ),
            0,
        )

    async def reset(self) -> None:
        await attempts.check(self.publisher.storage, self.publisher.claim)
        await self.publisher.flush_display()
        await self.stream.create(self.durable_sequence())
        self._next_flush = asyncio.get_running_loop().time() + self.flush_seconds

    async def append(self, values: tuple[dict[str, JsonValue], ...]) -> None:
        last = self.publisher.fold.display.segments[-1].event_sequence
        for sequence, value in enumerate(values, start=last - len(values) + 1):
            metadata = value.get("metadata")
            if isinstance(metadata, dict) and metadata.get("display") is False:
                continue
            result = await self.stream.append(sequence, value)
            if result == -1:
                await self.reset()
                return
            if result == -2:
                await self.publisher.flush_display()
                if not await self.stream.trim(self.durable_sequence()):
                    await self.reset()
                return  # The entire observed batch is now in the snapshot.
        if asyncio.get_running_loop().time() >= self._next_flush:
            await self.publisher.flush_display()
            if not await self.stream.trim(self.durable_sequence()):
                await self.reset()
            self._next_flush = asyncio.get_running_loop().time() + self.flush_seconds

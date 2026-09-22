"""Real Redis exact replay bounds, durable-prefix trimming and missing-stream refusal."""

import asyncio

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.streams import AttemptStream, Bounds
from a13n_service.runs.wakeups import KEY, notify
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio


async def test_exact_capacity_floor_expiry_and_attempt_isolation(redis_url):
    async with Redis.from_url(redis_url, decode_responses=True) as redis:
        first = AttemptStream(redis, "run_test", 1, Bounds(count=2, bytes=256, entry_bytes=128, ttl=1))
        second = AttemptStream(redis, "run_test", 2, Bounds(count=2, bytes=256, entry_bytes=128))
        assert await first.append(1, {"text": "missing"}) == -1
        assert await redis.exists(*first.keys) == 0
        await first.create(0)
        await second.create(0)
        assert await first.append(1, {"text": "first"}) == 1
        assert await first.append(2, {"text": "second"}) == 1
        assert await first.append(3, {"text": "unpersisted"}) == -2
        assert (await first.read(0)) == (0, [(1, {"text": "first"}), (2, {"text": "second"})])
        assert await redis.xlen(first.keys[0]) == 2
        assert await second.append(1, {"text": "new attempt"}) == 1
        assert await first.trim(1)
        assert await first.read(0) == (1, [])
        assert await first.append(3, {"text": "third"}) == 1
        assert [seq for seq, _ in (await first.read(1))[1]] == [2, 3]
        assert await second.read(0) == (0, [(1, {"text": "new attempt"})])
        with pytest.raises(ServiceError, match="entry limit"):
            await first.append(4, {"text": "x" * 128})
        assert await first.read(99) == (-2, [])
        # Appending is incapable of extending expiry or resurrecting the stream.
        async with asyncio.timeout(3):
            while await redis.exists(first.keys[0]):
                await asyncio.sleep(0.03)
        assert await first.append(4, {"text": "late"}) == -1
        assert await redis.exists(*first.keys) == 0
        assert await second.read(0) == (0, [(1, {"text": "new attempt"})])
        await asyncio.gather(*[notify(redis, timeout=1) for _ in range(100)])
        assert await redis.lrange(KEY, 0, -1) == ["wake"]


async def test_byte_limit_is_atomic_and_trim_releases_exact_bytes(redis_url):
    async with Redis.from_url(redis_url, decode_responses=True) as redis:
        stream = AttemptStream(redis, "run_bytes", 1, Bounds(count=10, bytes=30, entry_bytes=30))
        await stream.create(0)
        assert await stream.append(1, {"text": "1234567890"}) == 1
        assert await stream.append(2, {"text": "1234567890"}) == -2
        assert await redis.hget(stream.keys[1], "bytes") == "21"
        await stream.trim(1)
        assert await redis.hget(stream.keys[1], "bytes") == "0"
        assert await stream.append(2, {"text": "1234567890"}) == 1

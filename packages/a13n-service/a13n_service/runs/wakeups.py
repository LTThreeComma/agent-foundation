"""Capacity-one hints; only PostgreSQL decides which worker owns a run."""

import asyncio

from redis.asyncio import Redis
from redis.exceptions import RedisError

KEY = "a13n:service:claim:wake"
_PUSH = "if redis.call('LLEN', KEYS[1]) == 0 then return redis.call('RPUSH', KEYS[1], 'wake') end return 0"


async def notify(redis: Redis, *, timeout: float) -> None:
    try:
        async with asyncio.timeout(timeout):
            await redis.eval(_PUSH, 1, KEY)
    except (RedisError, TimeoutError):
        pass  # A committed run remains discoverable by the non-postponed periodic scan.


async def wait(redis: Redis, *, seconds: float) -> None:
    try:
        async with asyncio.timeout(seconds):
            await redis.blpop(KEY, timeout=seconds)
    except TimeoutError:
        pass
    except RedisError:
        await asyncio.sleep(min(seconds, 1))

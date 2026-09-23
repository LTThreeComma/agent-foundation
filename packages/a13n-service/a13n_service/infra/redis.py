"""Bounded accelerators and rate limits; never execution authority."""

import hashlib

from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.infra.errors import ServiceError

_RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""


async def rate_limit(client: Redis, identity: str, *, limit: int, window_seconds: int) -> None:
    key = "a13n:service:rate:" + hashlib.sha256(identity.encode()).hexdigest()
    try:
        count, remaining = await client.eval(_RATE_LIMIT, 1, key, window_seconds)
    except RedisError:
        raise ServiceError("unavailable", "Request limiter unavailable", {"dependency": "redis"}) from None
    if count > limit:
        raise ServiceError("rate_limited", "Too many requests", {"retry_after": max(1, remaining)})

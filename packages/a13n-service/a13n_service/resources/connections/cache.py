"""Short-lived discovery hints; live tool preparation remains authoritative."""

import asyncio

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.providers.tools import RESPONSE_BYTES
from a13n_service.resources.connections.schemas import ConnectionTest
from a13n_service.resources.connections.service import ResolvedConnection


def key(connection: ResolvedConnection) -> str:
    return f"a13n:connection-tools:{connection.organization_id}:{connection.id}:{connection.version}"


async def read(redis: Redis, connection: ResolvedConnection) -> ConnectionTest | None:
    if connection.auth in {"oauth", "managed"}:
        return None
    try:
        async with asyncio.timeout(0.2):
            content = await redis.get(key(connection))
        if content is None or len(content) > RESPONSE_BYTES:
            return None
        result = ConnectionTest.model_validate_json(content)
        if (
            result.connection_id == connection.id
            and result.version == connection.version
            and all(tool.permission_id for tool in result.tools)
        ):
            return result
    except (RedisError, TimeoutError, ValidationError):
        pass
    return None


async def write(redis: Redis, connection: ResolvedConnection, result: ConnectionTest) -> None:
    if connection.auth in {"oauth", "managed"}:
        return
    content = result.model_dump_json()
    if len(content.encode()) > RESPONSE_BYTES:
        return
    try:
        async with asyncio.timeout(0.2):
            await redis.set(key(connection), content, ex=30)
    except (RedisError, TimeoutError):
        pass

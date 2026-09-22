"""Shared relay startup and reserved response-reader capacity."""

from contextlib import AsyncExitStack

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY

from a13n_service_legacy.environments.websocket.relay_capability import validate_relay_backend
from a13n_service_legacy.environments.websocket.relay_runtime import RelayResponseRuntime
from a13n_service_legacy.ids import new_object_id
from a13n_service_legacy.settings import Settings
from a13n_service_legacy.storage import StorageResources
from a13n_service_legacy.storage.config import RedisServerConfig
from a13n_service_legacy.storage.redis import open_redis


async def build_relay_responses(
    settings: Settings,
    storage: StorageResources,
    catalog: ProviderCatalog[EnvironmentProviderDefinition],
    stack: AsyncExitStack,
) -> RelayResponseRuntime | None:
    if WEBSOCKET_PROVIDER_KEY not in catalog:
        return None
    configuration = settings.redis_config()
    if not isinstance(configuration, RedisServerConfig):
        raise ValueError("Client WebSocket Environments require a shared Redis server")
    await validate_relay_backend(storage.redis)
    reader = await stack.enter_async_context(open_redis(configuration.model_copy(update={"max_connections": 1})))
    responses = RelayResponseRuntime(storage.redis, reader, new_object_id("svc"))
    await responses.prepare()
    stack.push_async_callback(responses.close)
    return responses

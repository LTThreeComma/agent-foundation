"""Fresh Harness adapters for one environment instance, built from plain values after the session closed.

The registry's Harness definition is the provider contract: `create()` builds a single-use adapter from the
provider account, the instance's recipe and its portable state. Lifecycle operations use it once and close it;
execution hands it to the Harness, which enters and closes it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import anyio
from a13n_harness.providers.environment.management import Environment
from a13n_harness.providers.environment.models import EnvironmentState
from a13n_logging import get_logger
from pydantic import JsonValue

from a13n_service.providers.registry import Registry
from a13n_service.resources.providers.service import ResolvedProvider
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)
_CLOSE_SECONDS = 10


@dataclass(frozen=True, slots=True)
class Target:
    """What building an adapter for one instance needs, detached from the database."""

    environment_id: str
    provider: ResolvedProvider = field(repr=False)
    recipe: Mapping[str, JsonValue]
    state: EnvironmentState | None


async def construct(runtime: Runtime, target: Target, *, operation_id: str | None, allow_create: bool) -> Environment:
    """A fresh, unentered adapter. `allow_create=False` connects to the existing instance and never creates,
    starts or replaces one; the Harness enforces that connect-only providers never receive `True`."""
    provider, registry = target.provider, runtime.registry
    await registry.check_environment_endpoint(provider.type, provider.config, runtime.endpoint_policy)
    return await registry.get("environment", provider.type).create(
        dict(target.recipe),
        configuration=provider.config,
        credential=provider.reveal_credential(runtime.keys),
        environment_id=target.environment_id,
        state=target.state,
        operation_id=operation_id,
        allow_create=allow_create,
    )


async def close(adapter: Environment) -> None:
    """Release the adapter's local resources, boundedly and even when cancelled; the instance itself stays."""
    with anyio.CancelScope(shield=True), anyio.move_on_after(_CLOSE_SECONDS):
        try:
            await adapter.close()
        except Exception as error:
            logger.warning("Environment adapter close failed", extra={"error_type": type(error).__name__})


def provider_identity(registry: Registry, provider: ResolvedProvider) -> dict[str, JsonValue]:
    """The provider type and non-secret backend locator an instance's handle is meaningful in."""
    definition = registry.get("environment", provider.type)
    return {
        "type": provider.type,
        "backend": definition.backend_identity(definition.configuration_model.model_validate(provider.config)),
    }

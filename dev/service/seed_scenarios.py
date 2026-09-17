"""Independent seed scenario branches with explicit history dependencies."""

from time import perf_counter

import anyio
from a13n_service.settings import Settings

from .seed_client import Client
from .seed_connectivity import connectivity
from .seed_environments import environments
from .seed_execution import execution
from .seed_journeys import journeys, run
from .seed_lifecycle import resource_history
from .seed_sessions import bulk_sessions


async def scenarios(
    client: Client, base: str, catalog: dict, settings: Settings, identity: dict, model_url: str, session_count: int
) -> dict:
    result = {}

    async def conversations():
        started = perf_counter()
        runs, workspaces = await bulk_sessions(client, base, catalog, settings, session_count)
        result["bulk_environments"] = workspaces
        previous = next(item for item in runs if item["status"] == "completed")
        for index in range(12):
            previous = await run(
                client,
                base,
                previous["agent_id"],
                f"[long] Follow-up {index + 1}: expand the review.",
                previous=previous,
            )
        result["long_thread_id"] = previous["thread_id"]
        result["conversations"] = await journeys(client, base, catalog, previous)
        print(f"Seed conversation branch: {perf_counter() - started:.2f}s", flush=True)

    async def execution_scenarios():
        result["execution"] = await execution(client, base, catalog)

    async def connectivity_scenarios():
        result["connectivity"] = await connectivity(client, base, catalog, identity, model_url)

    async def environment_scenarios():
        result["environments"] = await environments(client, base, catalog, settings)

    # Conversations use isolated bulk Environments. Publication alone uses the
    # shared fixture Environment; the other branches create their own resources.
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(conversations)
        tasks.start_soon(execution_scenarios)
        tasks.start_soon(connectivity_scenarios)
        tasks.start_soon(environment_scenarios)
    result["conversations"].update(result.pop("execution"))
    catalog["scenarios"].update(result.pop("environments"))
    # Mutate revisions and delete resources only after every reader has finished.
    catalog["scenarios"].update(await resource_history(client, base, catalog))
    return result

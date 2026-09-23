"""Bounded periodic background work over durable evidence.

Each sweep is one named function that does a bounded amount of work per call. The scheduler only adds
interval, jitter, a deadline and failure logging; coordination between replicas belongs to the sweep
itself (row claims or `SKIP LOCKED`), and no sweep holds a database session across external I/O.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from a13n_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Sweep:
    name: str
    every: float
    run: Callable[[], Awaitable[object]]
    timeout: float


async def _loop(sweep: Sweep) -> None:
    # Jitter spreads replicas started together; later passes keep the configured cadence.
    await asyncio.sleep(random.uniform(0, sweep.every))
    while True:
        try:
            async with asyncio.timeout(sweep.timeout):
                await sweep.run()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Sweep failed", extra={"sweep": sweep.name, "error_type": type(error).__name__})
        await asyncio.sleep(sweep.every)


async def run_sweeps(sweeps: Sequence[Sweep]) -> None:
    names = [sweep.name for sweep in sweeps]
    if len(names) != len(set(names)):
        raise ValueError("Sweep names must be unique")
    async with asyncio.TaskGroup() as group:
        for sweep in sweeps:
            group.create_task(_loop(sweep), name=f"sweep-{sweep.name}")

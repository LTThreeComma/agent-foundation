"""Supervision contract for process-owned background components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial

from a13n_service.background import PeriodicTask, Sweep


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    """One critical component that must run for the process lifetime."""

    name: str
    run: Callable[[], Awaitable[None]]
    return_is_expected: Callable[[], bool] | None = None
    shutdown: Callable[[], Awaitable[None]] | None = None
    drain: Callable[[], None] | None = None


def periodic_task(
    name: str,
    scan: Callable[[], Awaitable[Sweep]],
    *,
    interval_seconds: float,
    timeout_seconds: float,
    drain: Callable[[], None] | None = None,
) -> BackgroundTask:
    """Keep a domain scan's polling and shutdown under process ownership."""
    loop = PeriodicTask(name, scan, interval_seconds=interval_seconds, timeout_seconds=timeout_seconds)

    def begin_drain() -> None:
        loop.drain()
        if drain is not None:
            drain()

    return BackgroundTask(
        name,
        loop.run,
        loop.is_draining,
        partial(loop.shutdown, timeout_seconds=5),
        begin_drain,
    )


async def shutdown_background_components(components: Sequence[BackgroundTask]) -> None:
    """Finish already-draining components in dependency order before cancellation."""
    for component in components:
        if component.shutdown is not None:
            await component.shutdown()


async def run_critical_component(
    name: str,
    run: Callable[[], Awaitable[None]],
    return_is_expected: Callable[[], bool] | None = None,
) -> None:
    """Fail the process if a critical component returns normally."""

    await run()
    if return_is_expected is not None and return_is_expected():
        return
    raise RuntimeError(f"critical component returned unexpectedly: {name}")


__all__ = ["BackgroundTask", "periodic_task", "run_critical_component", "shutdown_background_components"]

"""The worker process loop: reserve a slot, claim, execute each attempt under a supervised lease.

The periodic scan is the mechanism; the Redis marker only makes it run sooner. A full worker leaves markers
for others and rescans when a slot frees. Shutdown stops claiming, asks running attempts to hand off at
their next safe boundary and waits up to the drain deadline; unfinished attempts then rely on lease expiry.
"""

import asyncio
import socket
from collections.abc import Callable, Coroutine
from importlib.metadata import version
from typing import Any

from a13n_logging import get_logger

from a13n_service.infra.ids import new_object_id
from a13n_service.infra.redis import wait_for_wake
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost, renew
from a13n_service.runs.claim import claim
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

type Execute = Callable[[Runtime, Lease, AttemptControl], Coroutine[Any, Any, None]]


class Worker:
    def __init__(self, runtime: Runtime, execute: Execute):
        self.runtime, self.execute = runtime, execute
        # One ID per worker incarnation, recorded on each attempt; the start log maps it to its host.
        self.id = new_object_id("wrk")
        self.build = version("a13n-service")
        self.free = runtime.settings.worker.slots
        self.slot_released = asyncio.Event()
        self.running: dict[str, tuple[asyncio.Task[None], AttemptControl]] = {}
        self.stopping = False

    async def run(self) -> None:
        settings = self.runtime.settings.worker
        logger.info("Worker started", extra={"worker_id": self.id, "host": socket.gethostname(), "build": self.build})
        async with asyncio.TaskGroup() as group:
            try:
                while not self.stopping:
                    if self.free == 0:
                        self.slot_released.clear()
                        await self.slot_released.wait()
                        continue
                    requested = self.free
                    sent = asyncio.get_running_loop().time()
                    try:
                        leases = await claim(self.runtime, worker_id=self.id, worker_build=self.build, limit=requested)
                    except Exception as error:
                        # Running attempts keep their slots and leases; the next scan tries again.
                        logger.warning("Claim failed", extra={"error_type": type(error).__name__})
                        await asyncio.sleep(settings.scan_seconds)
                        continue
                    for lease in leases:
                        self.free -= 1
                        control = AttemptControl(
                            deadline=sent + settings.lease_seconds, renewal_margin=settings.lease_seconds / 3
                        )
                        task = group.create_task(self._attempt(lease, control), name=f"attempt-{lease.attempt_id}")
                        self.running[lease.attempt_id] = (task, control)
                    if len(leases) < requested:
                        await wait_for_wake(self.runtime.redis, timeout=settings.scan_seconds)
            except asyncio.CancelledError:
                await self._drain()
                raise

    async def _drain(self) -> None:
        self.stopping = True
        for _, control in self.running.values():
            control.handoff.set()
        tasks = [task for task, _ in self.running.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.runtime.settings.worker.drain_seconds)

    async def _attempt(self, lease: Lease, control: AttemptControl) -> None:
        try:
            execution = asyncio.create_task(self.execute(self.runtime, lease, control))
            supervisor = asyncio.create_task(self._supervise(lease, control, execution))
            try:
                await execution
            finally:
                supervisor.cancel()
        except LeaseLost:
            logger.info("Attempt lost its lease", extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id})
        except asyncio.CancelledError:
            logger.info("Attempt stopped", extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id})
            # The supervisor cancelling the execution ends only the attempt; cancelling this task ends the worker.
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
        except Exception as error:
            # Execution seals its own failures; anything escaping is left to lease expiry and recovery.
            logger.exception("Attempt crashed", extra={"run_id": lease.run_id, "error_type": type(error).__name__})
        finally:
            self.running.pop(lease.attempt_id, None)
            self.free += 1
            self.slot_released.set()

    async def _supervise(self, lease: Lease, control: AttemptControl, execution: asyncio.Task[None]) -> None:
        """Renew every third of the lease; poll cancellation and the principal's authority every authority interval.

        A renewal not confirmed within one authority interval has failed. Once the lease has missed its renewal,
        stop the execution: another worker may take over once the lease expires, and a stale attempt must not
        keep dispatching.
        """
        settings = self.runtime.settings.worker
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(settings.authority_seconds)
            sent = loop.time()
            extend = control.deadline - sent <= settings.lease_seconds * 2 / 3
            try:
                async with asyncio.timeout(settings.authority_seconds):
                    stop = await renew(
                        self.runtime.storage,
                        self.runtime.access,
                        lease,
                        seconds=settings.lease_seconds if extend else None,
                    )
            except LeaseLost:
                execution.cancel()
                return
            except Exception as error:
                logger.warning("Lease renewal failed", extra={"error_type": type(error).__name__})
                if control.renewal_missed():
                    execution.cancel()
                    return
                continue
            if extend:
                control.deadline = sent + settings.lease_seconds
            if stop is not None:
                control.stop(stop)

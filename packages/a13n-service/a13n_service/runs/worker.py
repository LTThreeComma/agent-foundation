"""The worker process loop: reserve a slot, claim, execute each attempt under a supervised lease.

The periodic scan is the mechanism; the Redis marker only makes it run sooner. A full worker leaves markers
for others and rescans when a slot frees. Shutdown stops claiming, asks running attempts to hand off at
their next safe boundary and waits up to the drain deadline; unfinished attempts then rely on lease expiry.
"""

import asyncio
import socket
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from a13n_logging import get_logger

from a13n_service.infra.ids import new_object_id
from a13n_service.infra.redis import wait_for_wake
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost
from a13n_service.runs.claim import claim
from a13n_service.runs.renewals import BATCH_SIZE, renew
from a13n_service.runs.runtime import Runtime

logger = get_logger(__name__)

type Execute = Callable[[Runtime, Lease, AttemptControl], Coroutine[Any, Any, None]]


@dataclass
class _Running:
    lease: Lease
    control: AttemptControl
    task: asyncio.Task[None]
    # A lost lease stops being renewed even while cancellation cleanup is still running.
    renewing: bool = True

    def cancel(self) -> None:
        self.renewing = False
        self.task.cancel()


class Worker:
    def __init__(self, runtime: Runtime, execute: Execute):
        self.runtime, self.execute = runtime, execute
        # One ID per worker incarnation, recorded on each attempt; the start log maps it to its host.
        self.id = new_object_id("wrk")
        self.build = version("a13n-service")
        self.free = runtime.settings.worker.slots
        self.slot_released = asyncio.Event()
        self.running: dict[str, _Running] = {}
        # Stable membership until an attempt exits; separate supervisors isolate batches that time out.
        self._groups: list[dict[str, _Running]] = [{} for _ in range((self.free + BATCH_SIZE - 1) // BATCH_SIZE)]
        self.stopping = False

    async def run(self) -> None:
        settings = self.runtime.settings.worker
        logger.info("Worker started", extra={"worker_id": self.id, "host": socket.gethostname(), "build": self.build})
        async with asyncio.TaskGroup() as group:
            supervisors = [group.create_task(self._supervise(members)) for members in self._groups]
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
                        members = min(self._groups, key=len)
                        task = group.create_task(
                            self._attempt(lease, control, members), name=f"attempt-{lease.attempt_id}"
                        )
                        self.running[lease.attempt_id] = members[lease.attempt_id] = _Running(lease, control, task)
                    if len(leases) < requested:
                        await wait_for_wake(self.runtime.redis, timeout=settings.scan_seconds)
            except asyncio.CancelledError:
                await self._drain()
                raise
            finally:
                for supervisor in supervisors:
                    supervisor.cancel()

    async def _drain(self) -> None:
        self.stopping = True
        for running in self.running.values():
            running.control.handoff.set()
        tasks = [running.task for running in self.running.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=self.runtime.settings.worker.drain_seconds)

    async def _attempt(self, lease: Lease, control: AttemptControl, members: dict[str, _Running]) -> None:
        try:
            await self.execute(self.runtime, lease, control)
        except LeaseLost:
            logger.info("Attempt lost its lease", extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id})
        except asyncio.CancelledError:
            logger.info("Attempt stopped", extra={"run_id": lease.run_id, "attempt_id": lease.attempt_id})
            raise  # A cancelled child task does not cancel the worker's TaskGroup.
        except Exception as error:
            # Execution seals its own failures; anything escaping is left to lease expiry and recovery.
            logger.exception("Attempt crashed", extra={"run_id": lease.run_id, "error_type": type(error).__name__})
        finally:
            members.pop(lease.attempt_id, None)
            self.running.pop(lease.attempt_id, None)
            self.free += 1
            self.slot_released.set()

    async def _supervise(self, members: dict[str, _Running]) -> None:
        """One bounded group, with independent transactions and per-attempt deadlines and stop signals.

        Only committed results advance deadlines. A skipped row or a failed batch retains each deadline;
        an attempt that missed renewal stops alone. Finished registrations ignore any late result.
        """
        settings = self.runtime.settings.worker
        loop = asyncio.get_running_loop()
        next_poll = loop.time() + settings.authority_seconds
        while True:
            await asyncio.sleep(max(0, next_poll - loop.time()))
            sent = loop.time()
            next_poll = sent + settings.authority_seconds
            batch = [running for running in members.values() if running.renewing and not running.task.done()]
            if not batch:
                continue
            try:
                async with asyncio.timeout(settings.renewal_timeout):
                    results = await renew(
                        self.runtime.storage,
                        self.runtime.access,
                        [running.lease for running in batch],
                        seconds=settings.lease_seconds,
                    )
            except Exception as error:
                logger.warning(
                    "Lease batch renewal failed",
                    extra={"worker_id": self.id, "attempt_count": len(batch), "error_type": type(error).__name__},
                )
                results = {}
            for running in batch:
                attempt_id, control = running.lease.attempt_id, running.control
                if members.get(attempt_id) is not running or running.task.done():
                    continue
                result = results.get(attempt_id)
                if result is not None and result.status == "renewed":
                    control.deadline = sent + settings.lease_seconds
                    if result.outcome is not None:
                        control.stop(result.outcome)
                elif (result is not None and result.status == "lost") or control.renewal_missed():
                    running.cancel()

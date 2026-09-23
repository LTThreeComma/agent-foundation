"""One bounded claim loop and independently supervised attempt authority."""

import asyncio
from importlib.metadata import version

from a13n_harness.errors import RunError
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model import ModelProviderDefinition
from a13n_logging import exception_details, get_logger
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.providers.tools import ToolSourceDefinition
from a13n_service.runs import attempts, seal, selection, wakeups
from a13n_service.runs.execute import execute
from a13n_service.runs.policy import AdmissionPolicy, CallCheck
from a13n_service.runs.schemas import AttemptClaim
from a13n_service.settings import Settings

logger = get_logger(__name__)


class Worker:
    def __init__(
        self,
        storage: Storage,
        objects: LocalObjects,
        redis: Redis,
        *,
        config: Settings,
        catalog: ProviderCatalog[ModelProviderDefinition],
        tool_catalog: ProviderCatalog[ToolSourceDefinition],
        keys: KeyRing,
        endpoint_policy: EndpointPolicy,
        admission: AdmissionPolicy | None,
    ):
        self.storage, self.objects, self.redis, self.config = storage, objects, redis, config
        self.tool_catalog = tool_catalog
        self.catalog, self.keys, self.endpoint_policy, self.admission = catalog, keys, endpoint_policy, admission
        self.id = new_object_id("wrk")
        self._active: set[asyncio.Task[None]] = set()
        self._slot_released = asyncio.Event()

    async def _heartbeat(self, claim: AttemptClaim) -> None:
        interval = self.config.worker.lease_seconds / 3
        while True:
            async with asyncio.timeout(interval):
                await attempts.heartbeat(self.storage, claim, lease_seconds=self.config.worker.lease_seconds)
            await asyncio.sleep(interval)

    async def _authority(self, claim: AttemptClaim) -> None:
        async with asyncio.timeout(self.config.worker.lease_seconds / 3):
            _, model = await selection.load(self.storage, claim)
        check = CallCheck(self.storage, claim, model, self.admission)
        while True:
            await asyncio.sleep(self.config.worker.authority_seconds)
            async with asyncio.timeout(self.config.worker.lease_seconds / 3):
                await check.refresh()

    async def _attempt(self, claim: AttemptClaim) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(claim))
        authority = asyncio.create_task(self._authority(claim))
        work = asyncio.create_task(
            execute(
                self.storage,
                self.objects,
                claim,
                config=self.config,
                redis=self.redis,
                catalog=self.catalog,
                tool_catalog=self.tool_catalog,
                keys=self.keys,
                endpoint_policy=self.endpoint_policy,
                admission=self.admission,
            )
        )
        tasks = (work, heartbeat, authority)
        error = None
        try:
            async with asyncio.timeout(self.config.worker.attempt_seconds):
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                (work if work in done else next(iter(done))).result()
        except asyncio.CancelledError:
            raise
        except Exception as caught:
            error = caught
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if error is not None and not isinstance(error, attempts.LeaseLost):
            # Cancellation stops supported I/O before failure; unknown external effects are never replayed here.
            harness_failure = {}
            if isinstance(error, RunError):
                kind = error.details.get("exception_type")
                status = error.details.get("status_code")
                if isinstance(kind, str) and len(kind) <= 128 and kind.isascii() and kind.isidentifier():
                    harness_failure["exception_type"] = kind
                if type(status) is int and 100 <= status <= 599:
                    harness_failure["status_code"] = status
            logger.warning(
                "Run execution stopped",
                extra={
                    "run_id": claim.run_id,
                    "attempt_id": claim.attempt_id,
                    "error_type": type(error).__name__,
                    "exception_details": exception_details(error),
                    "harness_failure": harness_failure,
                },
            )
            code = error.code if isinstance(error, ServiceError) else "execution_failed"
            message = (
                error.message if isinstance(error, ServiceError) else "Execution failed; inspect worker diagnostics"
            )
            try:
                await seal.failed(self.storage, claim, code=code, message=message)
            except attempts.LeaseLost:
                pass

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._active.discard(task)
        self._slot_released.set()
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.warning("Attempt settlement failed", extra={"error_type": type(error).__name__})

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        next_scan = loop.time()
        try:
            while True:
                self._slot_released.clear()
                while len(self._active) < self.config.worker.slots:
                    try:
                        claim = await attempts.claim_run(
                            self.storage,
                            worker_id=self.id,
                            worker_build=version("a13n-service"),
                            lease_seconds=self.config.worker.lease_seconds,
                        )
                    except SQLAlchemyError as error:
                        logger.warning("Claim scan failed", extra={"error_type": type(error).__name__})
                        await asyncio.sleep(self.config.worker.scan_seconds)
                        break
                    if claim is None:
                        break
                    task = asyncio.create_task(self._attempt(claim), name=f"attempt-{claim.attempt_id}")
                    self._active.add(task)
                    task.add_done_callback(self._finished)
                now = loop.time()
                if now >= next_scan:
                    next_scan = now + self.config.worker.scan_seconds
                delay = max(0.001, next_scan - now)
                slot = asyncio.create_task(self._slot_released.wait())
                wake = (
                    asyncio.create_task(wakeups.wait(self.redis, seconds=delay))
                    if len(self._active) < self.config.worker.slots
                    else asyncio.create_task(asyncio.sleep(delay))
                )
                try:
                    await asyncio.wait((slot, wake), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    slot.cancel()
                    wake.cancel()
                    await asyncio.gather(slot, wake, return_exceptions=True)
        finally:
            active = tuple(self._active)
            for task in active:
                task.cancel()
            if active:
                async with asyncio.timeout(self.config.server.shutdown_timeout):
                    await asyncio.gather(*active, return_exceptions=True)

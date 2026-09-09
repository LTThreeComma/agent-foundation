"""One-shot fault barriers in the explicit test Host, around real operations.

No production executable imports this module. The parent arms private files;
the child reports the exact boundary and then waits, fails, or exits. Wrappers
preserve the real storage calls and recovery code, including CAS and SQL commit.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import anyio
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class FaultPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    point: str
    role: Literal["worker", "control", "all"] = "worker"
    where: dict = Field(default_factory=dict)
    action: Literal["pause", "timeout", "unavailable", "rollback", "crash"] = "pause"
    pause: bool = True
    shield: bool = False
    hits: int = Field(default=1, ge=1, le=20)
    timeout_seconds: float = Field(default=180, gt=0, le=300)


def matches(where, observed):
    return all(
        value in observed.get("run_ids", [])
        if key == "run_id" and "run_ids" in observed
        else observed.get(key) == value
        for key, value in where.items()
    )


def state_context(state):
    envelope = getattr(state, "envelope", state)
    result = {
        "run_id": envelope.run_id,
        "thread_id": envelope.thread_id,
        "kind": envelope.checkpoint_kind,
        "seq": envelope.checkpoint_seq,
        "receipts": bool(envelope.host.inbox_receipts),
        "fence": getattr(state, "writer_fence", envelope.last_checkpoint_fence),
    }
    if hasattr(state, "info"):
        result.update(version=state.info.version, digest=state.digest_sha256, key=state.info.key)
    return result


@dataclass
class Ticket:
    path: Path
    plan: FaultPlan
    observed: dict

    async def perform(self):
        if self.plan.pause:
            with anyio.fail_after(self.plan.timeout_seconds):
                while not await anyio.Path(self.path.with_suffix(".release")).exists():
                    await anyio.sleep(0.02)
        if self.plan.action == "timeout":
            raise TimeoutError("live_test_lost_storage_acknowledgement")
        if self.plan.action == "unavailable":
            from a13n_service.storage import ObjectStoreUnavailable

            raise ObjectStoreUnavailable("live_test_storage_unavailable")
        if self.plan.action == "rollback":
            from sqlalchemy.exc import OperationalError

            raise OperationalError("live_test_before_commit", {}, RuntimeError("injected rollback"))
        if self.plan.action == "crash":
            os._exit(86)
        if self.plan.action != "pause":
            raise ValueError(f"Unknown test fault action: {self.plan.action}")


class Faults:
    def __init__(self, root: Path, role: str):
        self.root, self.role = root, role
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _take(self, point, observed):
        for path in sorted(self.root.glob("*.json")):
            if path.name.endswith(".hit.json"):
                continue
            try:
                plan = FaultPlan.model_validate_json(path.read_text())
            except FileNotFoundError:
                continue  # The parent may disarm a plan while the child scans.
            if plan.point != point or plan.role not in {self.role, "all"} or not matches(plan.where, observed):
                continue
            for index in range(plan.hits):
                claimed = path.with_suffix(f".{index}.claimed")
                try:
                    descriptor = os.open(claimed, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    continue
                os.close(descriptor)
                ticket_path = path.with_suffix(f".{index}")
                hit = {"point": point, "role": self.role, "pid": os.getpid(), **observed}
                temporary = ticket_path.with_suffix(f".{index}.hit.tmp")
                destination = path.with_suffix(f".{index}.hit.json")
                with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "w") as output:
                    json.dump(hit, output)
                if point == "redis.partial":
                    temporary.unlink()
                else:
                    temporary.replace(destination)
                # Release belongs to this exact plan/hit, never another process's operation.
                return Ticket(path.with_suffix(f".{index}.gate"), plan, hit)
        return None

    async def take(self, point, observed):
        ticket = await anyio.to_thread.run_sync(self._take, point, observed)
        if ticket is not None:
            logger.info("live_fault_reached point=%s role=%s evidence=%s", point, self.role, ticket.observed)
        return ticket

    async def hit(self, point, observed):
        ticket = await self.take(point, observed)
        if ticket is not None:
            with anyio.CancelScope(shield=ticket.plan.shield):
                await ticket.perform()

    def _record(self, point, observed):
        path = self.root / f"operations-{os.getpid()}.jsonl"
        with os.fdopen(os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600), "a") as output:
            output.write(json.dumps({"point": point, "role": self.role, "pid": os.getpid(), **observed}) + "\n")

    async def record(self, point, observed):
        await anyio.to_thread.run_sync(self._record, point, observed)


def _transaction_faults(stack, faults, module, name):
    original = module.transaction

    @asynccontextmanager
    async def scoped(*args, **kwargs):
        from a13n_service.interactions.models import RunAttemptRecord, RunRecord

        observed = {}
        async with original(*args, **kwargs) as session:
            yield session
            records = [*session.identity_map.values(), *session.new]
            runs = [row for row in records if isinstance(row, RunRecord)]
            attempts = [row for row in records if isinstance(row, RunAttemptRecord)]
            observed = {
                "run_ids": sorted({row.id for row in runs} | {row.run_id for row in attempts}),
                "statuses": sorted({row.status for row in runs}),
                "attempt_statuses": sorted({row.status for row in attempts}),
            }
            await faults.hit(name + ".commit.before", observed)
        # Real commit and connection cleanup have finished before this boundary.
        await faults.hit(name + ".commit.after", observed)

    stack.enter_context(patch.object(module, "transaction", scoped))


@contextmanager
def installed_faults(config, role):
    """Instrument only the lifetime of an explicitly configured recovery Host."""
    from a13n_service.interactions import acceptance, attempts, outcomes, queue_handoff
    from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler
    from a13n_service.interactions.objects import RunStateStore
    from a13n_service.object_retention.collector import ObjectCollector
    from a13n_service.run_stream.domain import PublicationUnavailable
    from a13n_service.run_stream.projector import LifecycleRunStreamProjector
    from a13n_service.run_stream.redis import _SCRIPT, RedisRunStream
    from a13n_service.storage.object_store import S3ObjectStore

    faults = Faults(Path(config["recovery_faults"]), role)
    with ExitStack() as stack:
        from a13n_harness.capabilities import context as context_capabilities
        from a13n_harness.capabilities.context import CompactionCapability, CompactionPolicy
        from a13n_service.agents.reconstruction import AgentReconstructor

        original_reconstructor = AgentReconstructor.__init__

        def reconstruct(self, catalog, *, capability_provider=None, **kwargs):
            def capabilities(context):
                provided = list(capability_provider(context)) if capability_provider else []
                if any(p.plugin_key == "live.recovery" and p.config.get("compact") for p in context.config.plugins):
                    provided.append(CompactionCapability(CompactionPolicy(trigger_tokens=1)))
                return provided

            # Reserved first-party capabilities belong to the trusted definition
            # provider. Plugins cannot claim Harness-owned capability identities.
            original_reconstructor(self, catalog, capability_provider=capabilities, **kwargs)

        stack.enter_context(patch.object(AgentReconstructor, "__init__", reconstruct))
        original_compacted_history = context_capabilities._build_compacted_history

        def summary_only_history(messages, summary, *, retained_requests):
            # The default compactor preserves current-turn inputs. Exercise the
            # Service receipt contract with a summary-only Host policy as well:
            # native summarization/checkpointing still run, but no raw input is
            # retained in the resulting model message list.
            return original_compacted_history(messages, summary, retained_requests=())

        stack.enter_context(patch.object(context_capabilities, "_build_compacted_history", summary_only_history))
        if config.get("recovery_budget"):
            from a13n_service.interactions import commands
            from a13n_service.interactions.domain import ExecutionBudget
            from a13n_service.temporal import utc_now

            def accepted_budget(**kwargs):
                policy = dict(config["recovery_budget"])
                if seconds := policy.pop("deadline_seconds", None):
                    policy["execution_deadline_at"] = utc_now() + timedelta(seconds=seconds)
                return ExecutionBudget(**{**kwargs, **policy})

            # Explicit test Host acceptance policy; no authoritative Run row is
            # edited after acceptance. Public callers cannot select these fields.
            stack.enter_context(patch.object(commands, "ExecutionBudget", accepted_budget))
        for module, name in (
            (acceptance, "acceptance"),
            (outcomes, "outcome"),
            (queue_handoff, "queue"),
            (attempts, "attempt"),
        ):
            _transaction_faults(stack, faults, module, name)

        def wrap_state(name, before):
            original = getattr(RunStateStore, name)

            @wraps(original)
            async def operation(self, *args, **kwargs):
                observed = state_context(before(args, kwargs))
                if name == "claim_writer":
                    observed["claim_fence"] = kwargs["attempt_number"]
                await faults.hit(f"state.{name}.before", observed)
                result = await original(self, *args, **kwargs)
                await faults.record(f"state.{name}.after", state_context(result))
                await faults.hit(f"state.{name}.after", state_context(result))
                return result

            stack.enter_context(patch.object(RunStateStore, name, operation))

        wrap_state("create", lambda args, kwargs: args[1])
        wrap_state("replace", lambda args, kwargs: args[1])
        wrap_state("claim_writer", lambda args, kwargs: args[0])

        original_put = S3ObjectStore.put

        @wraps(original_put)
        async def put(self, key, source, **kwargs):
            observed = {"key": key}
            if key.endswith("/state.json") and isinstance(source, bytes):
                body = json.loads(source)
                observed.update(
                    run_id=body["run_id"],
                    thread_id=body["thread_id"],
                    kind=body["checkpoint_kind"],
                    seq=body["checkpoint_seq"],
                    receipts=bool(body["host"].get("inbox_receipts")),
                    fence=int(kwargs.get("metadata", {}).get("writer-fence", 0)),
                )
            ticket = await faults.take("objects.put.before", observed)
            # A dispatched request can complete at storage after local cancellation.
            # Only tests explicitly selecting that in-flight window shield this call.
            with anyio.CancelScope(shield=ticket is not None and ticket.plan.shield):
                if ticket is not None:
                    await ticket.perform()
                result = await original_put(self, key, source, **kwargs)
                observed.update(version=result.version, digest=result.metadata.get("digest-sha256"))
                await faults.record("objects.put.after", observed)
                await faults.hit("objects.put.after", observed)
                return result

        stack.enter_context(patch.object(S3ObjectStore, "put", put))

        def wrap_object(name):
            original = getattr(S3ObjectStore, name)

            @wraps(original)
            async def operation(self, key, **kwargs):
                observed = {"key": key}
                ticket = await faults.take(f"objects.{name}.before", observed)
                with anyio.CancelScope(shield=ticket is not None and ticket.plan.shield):
                    if ticket:
                        await ticket.perform()
                    result = await original(self, key, **kwargs)
                    await faults.record(f"objects.{name}.after", observed)
                    await faults.hit(f"objects.{name}.after", observed)
                    return result

            stack.enter_context(patch.object(S3ObjectStore, name, operation))

        wrap_object("stat")
        wrap_object("delete")
        original_confirm = DatabaseThreadInboxReconciler.confirm_inbox_receipts

        @wraps(original_confirm)
        async def confirm(self, context, state):
            observed = state_context(state)
            await faults.hit("inbox.confirm.before", observed)
            result = await original_confirm(self, context, state)
            await faults.hit("inbox.confirm.after", observed)
            return result

        stack.enter_context(patch.object(DatabaseThreadInboxReconciler, "confirm_inbox_receipts", confirm))
        original_read = DatabaseThreadInboxReconciler.read_eligible

        @wraps(original_read)
        async def read_inbox(self, authority, configuration):
            await faults.hit("inbox.read.before", {"run_id": authority.run_id})
            return await original_read(self, authority, configuration)

        stack.enter_context(patch.object(DatabaseThreadInboxReconciler, "read_eligible", read_inbox))
        original_mutate = RedisRunStream._mutate

        @wraps(original_mutate)
        async def mutate(self, organization_id, run_id, operation, **kwargs):
            events = kwargs.get("events", ())
            observed = {
                "run_id": run_id,
                "operation": operation,
                "event_type": events[-1].event_type if events else None,
            }
            await faults.hit("redis.mutate.before", observed)
            partial = await faults.take("redis.partial", observed)
            if partial:
                # Execute the actual owning Lua up to its first committed event.
                marker = "    updates[#updates + 1] = 'event:' .. event.id"
                assert _SCRIPT.count(marker) == 1
                faulty = _SCRIPT.replace(marker, "error('live_test_partial_publication')\n" + marker)
                healthy = self._script
                self._script = self._redis.register_script(faulty)
                try:
                    await original_mutate(self, organization_id, run_id, operation, **kwargs)
                except PublicationUnavailable:
                    evidence_path = partial.path.with_suffix(".hit.json")
                    temporary = evidence_path.with_suffix(".tmp")
                    await anyio.Path(temporary).write_text(json.dumps(partial.observed))
                    temporary.replace(evidence_path)
                    await partial.perform()
                    raise
                finally:
                    self._script = healthy
                raise AssertionError("Partial Redis publication did not fail")
            result = await original_mutate(self, organization_id, run_id, operation, **kwargs)
            await faults.record("redis.mutate.after", observed)
            await faults.hit("redis.mutate.after", observed)
            return result

        stack.enter_context(patch.object(RedisRunStream, "_mutate", mutate))
        original_project = LifecycleRunStreamProjector._project_event

        @wraps(original_project)
        async def project(self, event):
            observed = {"run_id": event.run_id, "event_type": event.event_type, "event_id": event.id}
            await faults.hit("projection.before", observed)
            result = await original_project(self, event)
            await faults.hit("projection.after", observed)
            return result

        stack.enter_context(patch.object(LifecycleRunStreamProjector, "_project_event", project))
        from a13n_service.environments.lifecycle import EnvironmentLifecycle

        original_environment_publish = EnvironmentLifecycle.publish

        @wraps(original_environment_publish)
        async def environment_publish(self, operation, environment, **kwargs):
            state = environment.dump_state() if environment is not None else None
            error = kwargs.get("error")
            causes = []
            while error is not None and len(causes) < 5:
                causes.append({"type": type(error).__name__, "message": str(error)})
                error = error.__cause__
            observed = {
                "environment_id": operation.environment_id,
                "action": operation.action,
                "operation_id": operation.operation_id,
                "fence": operation.fence,
                "error": kwargs.get("error") is not None,
                "error_code": getattr(kwargs.get("error"), "code", None),
                "error_message": str(kwargs["error"]) if kwargs.get("error") else None,
                "error_causes": causes,
                "state": state.model_dump(mode="json") if state else None,
            }
            await faults.hit("environment.publish.before", observed)
            result = await original_environment_publish(self, operation, environment, **kwargs)
            await faults.record("environment.publish.after", observed)
            await faults.hit("environment.publish.after", observed)
            return result

        stack.enter_context(patch.object(EnvironmentLifecycle, "publish", environment_publish))
        from a13n_environment.docker.runtime import DockerSDKEngine

        original_start_container = DockerSDKEngine.start_container

        @wraps(original_start_container)
        async def start_container(self, container_id):
            await original_start_container(self, container_id)
            inspection = await self.inspect_container(container_id)
            observed = {
                "container_id": container_id,
                "environment_id": inspection.labels.get("io.a13n.environment-id"),
            }
            await faults.hit("docker.start.after", observed)

        stack.enter_context(patch.object(DockerSDKEngine, "start_container", start_container))
        if config.get("recovery_collection_seconds") is not None:
            original_init = ObjectCollector.__init__

            @wraps(original_init)
            def collector(self, *args, **kwargs):
                # Accelerate only the test lab's age policy; leases use the real clock.
                kwargs["minimum_age"] = timedelta(seconds=config["recovery_collection_seconds"])
                original_init(self, *args, **kwargs)

            stack.enter_context(patch.object(ObjectCollector, "__init__", collector))
        yield faults

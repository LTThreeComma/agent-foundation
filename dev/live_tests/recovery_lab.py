"""Independent observations and owned fault controls for cross-store journeys."""

from __future__ import annotations

import asyncio
import json
import signal
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio
from a13n_service.interactions.control_models import QueuedSubmissionRecord, ThreadInboxRecord
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.settings import Settings
from a13n_service.storage import ObjectNotFound, short_session
from a13n_service.storage.object_store import S3ObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from aiobotocore.config import AioConfig
from aiobotocore.httpxsession import HttpxSession
from aiobotocore.session import get_session
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from .management_support import ManagementJourney
from .recovery_faults import FaultPlan
from .round_two_lab import open_lab, private_json


class ArmedFault:
    def __init__(self, path, plan):
        self.path, self.plan = path, plan
        temporary = path.with_suffix(".plan.tmp")
        private_json(temporary, plan.model_dump())
        temporary.replace(path)

    async def reached(self, *, index=0):
        evidence = self.path.with_suffix(f".{index}.hit.json")
        with anyio.move_on_after(90):
            while not evidence.exists():
                await anyio.sleep(0.02)
        assert evidence.exists(), f"Fault was not reached: {self.plan.point}, {self.plan.role}, {self.plan.where}"
        return json.loads(evidence.read_text())

    def release(self, *, index=0):
        self.path.with_suffix(f".{index}.release").touch()

    def close(self):
        self.path.unlink(missing_ok=True)
        for index in range(self.plan.hits):
            self.release(index=index)


class RecoveryJourney(ManagementJourney):
    def __init__(self, lab, sessions, objects, redis):
        super().__init__(lab)
        self.sessions, self.objects, self.redis = sessions, objects, redis
        self.states = RunStateStore(objects)
        self.faults = []

    def case_root(self, case):
        return self.lab.root / "workspace" / case["case_id"]

    async def marker(self, case, name):
        with anyio.fail_after(90):
            while not (self.case_root(case) / name).exists():
                await anyio.sleep(0.02)

    def release_marker(self, case, name):
        (self.case_root(case) / name).touch()

    def recovery_plugins(self, *, compact=False):
        return [
            {
                "instance_name": "recovery",
                "plugin_key": "live.recovery",
                "config": {"root": self.live.config["workspace_root"], "compact": compact},
            }
        ]

    def model_observations(self, case):
        path = self.case_root(case) / "recovery-observations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def arm(self, point, *, role="worker", where=None, **options):
        plan = FaultPlan(point=point, role=role, where=where or {}, **options)
        fault = ArmedFault(Path(self.lab.config["recovery_faults"]) / f"{uuid4().hex}.json", plan)
        self.faults.append(fault)
        return fault

    async def kill_at(self, fault, *, index=0):
        evidence = await fault.reached(index=index)
        process = next(process for process in self.lab.processes if process.pid == evidence["pid"])
        await self.lab.stop(process, signal.SIGKILL)
        return evidence

    async def state(self, run_id):
        return await self.states.read(self.live.config["organization_id"], run_id)

    async def snapshot(self, run_id):
        """Read authority without going through a failed Control or its fault proxy."""
        organization = self.live.config["organization_id"]
        async with short_session(self.sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(RunRecord.id == run_id, RunRecord.organization_id == organization)
            )
            if record is None:
                return None
            run = record.to_resource().model_dump(mode="json")
            thread = (await database.get(ThreadRecord, record.thread_id)).to_resource().model_dump(mode="json")
            attempts = list(
                await database.scalars(
                    select(RunAttemptRecord)
                    .where(RunAttemptRecord.run_id == run_id)
                    .order_by(RunAttemptRecord.attempt_number)
                )
            )
            inbox = list(
                await database.scalars(
                    select(ThreadInboxRecord)
                    .where(ThreadInboxRecord.thread_id == record.thread_id)
                    .order_by(ThreadInboxRecord.delivery_sequence)
                )
            )
            queue = list(
                await database.scalars(
                    select(QueuedSubmissionRecord)
                    .where(QueuedSubmissionRecord.thread_id == record.thread_id)
                    .order_by(QueuedSubmissionRecord.created_at)
                )
            )
            events = list(
                await database.scalars(
                    select(LifecycleEventRecord)
                    .where(LifecycleEventRecord.run_id == run_id)
                    .order_by(LifecycleEventRecord.seq)
                )
            )
            return {
                "run": run,
                "thread": thread,
                "attempts": [row.to_resource().model_dump(mode="json") for row in attempts],
                "inbox": [row.to_resource().model_dump(mode="json") for row in inbox],
                "queue": [row.to_resource().model_dump(mode="json") for row in queue],
                "events": [row.to_resource().model_dump(mode="json") for row in events],
            }

    async def sealed_consistently(self, run_id, outcome="completed"):
        run = await self.live.finish(run_id, outcome)
        snapshot = await self.snapshot(run_id)
        assert snapshot["run"]["status"] == outcome
        assert snapshot["run"]["current_run_attempt_id"] is None
        assert all(row["status"] in {"failed", "succeeded", "yielded", "cancelled"} for row in snapshot["attempts"])
        if outcome in {"completed", "waiting"}:
            state = await self.state(run_id)
            assert state.digest_sha256 == run["sealed_state_digest_sha256"]
            assert state.envelope.checkpoint_kind == outcome
            assert state.envelope.run_id == run_id and state.envelope.thread_id == run["thread_id"]
            assert snapshot["run"]["sealed_state"]["checkpoint_seq"] == state.envelope.checkpoint_seq
            if outcome == "completed":
                assert snapshot["thread"]["head_run_id"] == run_id
        path = self.lab.root / (run_id + "-settled.json")
        private_json(path, snapshot)
        return run

    async def wait_attempts(self, run_id, count):
        snapshot = await self.live.wait(
            lambda: self.snapshot(run_id), lambda value: len(value["attempts"]) == count, f"{count} durable Attempts"
        )
        return snapshot["attempts"]

    def operations(self, point, *, run_id=None):
        result = []
        for path in Path(self.lab.config["recovery_faults"]).glob("operations-*.jsonl"):
            for line in path.read_text().splitlines():
                item = json.loads(line)
                if item["point"] == point and (run_id is None or item.get("run_id") == run_id):
                    result.append(item)
        return result

    async def exists(self, key):
        try:
            await self.objects.stat(key)
        except ObjectNotFound:
            return False
        return True

    async def restart_control(self):
        for process in self.lab.controls:
            if self.lab.origins[process] == self.lab.config["control_url"] and process.returncode is None:
                await self.lab.stop(process, signal.SIGKILL)
        return await self.lab.start_control()

    async def restart_dependency(self, name, *, lose_redis=False):
        container = self.lab.containers[name]
        if name == "redis":
            if lose_redis:
                await self.redis.flushdb()  # This client names the newly allocated lab container only.
            await self.redis.save()
        await anyio.to_thread.run_sync(lambda: container.get_wrapped_container().restart(timeout=0))
        with anyio.fail_after(30):
            while True:
                try:
                    if name == "redis":
                        await self.redis.ping()
                    else:
                        async with short_session(self.sessions) as session:
                            await session.execute(text("SELECT 1"))
                    break
                except (OSError, RuntimeError, OperationalError, RedisError):
                    await anyio.sleep(0.1)

    @asynccontextmanager
    async def pending(self, awaitable):
        task = asyncio.create_task(awaitable)
        try:
            yield task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def open_recovery_lab(*, environment=None, policy=None, collection_seconds=None):
    overrides = {
        "A13N_SERVICE_LIFECYCLE_PROJECTION_POLL_INTERVAL_SECONDS": "0.1",
        "A13N_SERVICE_LIFECYCLE_PROJECTION_LEASE_SECONDS": "3",
        "A13N_SERVICE_LIFECYCLE_PROJECTION_RETRY_SECONDS": "0.1",
        "A13N_SERVICE_CONTROL_COLLECTION_POLL_INTERVAL_SECONDS": "0.2",
        "A13N_SERVICE_CONTROL_COLLECTION_TIMEOUT_SECONDS": "3",
        "A13N_SERVICE_OBJECT_PUBLICATION_TIMEOUT_SECONDS": "5",
        **(environment or {}),
    }
    async with open_lab(
        suite="recovery",
        environment_overrides=overrides,
        recovery_options={
            "recovery_budget": policy,
            "recovery_collection_seconds": collection_seconds,
        },
    ) as lab:
        async with AsyncExitStack() as stack:
            settings = Settings(_env_file=None, database_url=lab.environment["A13N_SERVICE_DATABASE_URL"])
            engine = create_sql_engine(settings.database_config())
            stack.push_async_callback(engine.dispose)
            redis = Redis.from_url(lab.environment["A13N_SERVICE_REDIS_URL"])
            stack.push_async_callback(redis.aclose)
            s3 = await stack.enter_async_context(
                get_session().create_client(
                    "s3",
                    endpoint_url=lab.environment["A13N_SERVICE_OBJECT_ENDPOINT_URL"],
                    region_name=lab.environment["A13N_SERVICE_OBJECT_REGION"],
                    aws_access_key_id=lab.environment.get("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=lab.environment.get("AWS_SECRET_ACCESS_KEY"),
                    config=AioConfig(
                        connect_timeout=2,
                        read_timeout=5,
                        proxies={},
                        retries={"total_max_attempts": 1},
                        s3={"addressing_style": "path"},
                        http_session_cls=HttpxSession,
                    ),
                )
            )
            journey = RecoveryJourney(
                lab,
                create_session_factory(engine),
                S3ObjectStore(s3, lab.environment["A13N_SERVICE_OBJECT_BUCKET"]),
                redis,
            )
            try:
                yield journey
            finally:
                for fault in journey.faults:
                    fault.close()
                for proxy in lab.proxies.values():
                    proxy.restore()
                if not any(
                    process.returncode is None and lab.origins[process] == lab.config["control_url"]
                    for process in lab.controls
                ):
                    await lab.start_control()

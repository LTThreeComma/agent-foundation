"""Real PostgreSQL locking across lifecycle writers in the same Session."""

import anyio
import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks import CreateHookSubscriptionRequest, WebhookDestinationConfig
from a13n_service.hooks.persistence import create_hook_subscription, write_hook_lifecycle
from a13n_service.interactions.domain import ThreadOriginKind, ThreadRole
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.records import run_record, thread_record
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, SECRET_ID, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio
SECOND_RUN_ID = "run_8282828282828282"
SECOND_THREAD_ID = "thread-82828282828282828282828282828282"


@pytest.mark.parametrize("subscribed", [False, True])
async def test_same_session_lifecycle_writers_commit_without_deadlock(
    hook_postgres_sessions: async_sessionmaker[AsyncSession], subscribed: bool
) -> None:
    sessions = hook_postgres_sessions
    await seed_run_and_secret(sessions)
    revision_id = None
    async with transaction(sessions) as database:
        original = await database.get(RunRecord, RUN_ID)
        thread = await database.get(ThreadRecord, THREAD_ID)
        assert original is not None and thread is not None
        database.add(
            thread_record(
                thread.to_resource().model_copy(
                    update={
                        "id": SECOND_THREAD_ID,
                        "role": ThreadRole.child,
                        "origin_kind": ThreadOriginKind.child,
                        "origin_thread_id": THREAD_ID,
                        "origin_run_id": RUN_ID,
                        "current_run_id": SECOND_RUN_ID,
                    }
                )
            )
        )
        database.add(
            run_record(original.to_resource().model_copy(update={"id": SECOND_RUN_ID, "thread_id": SECOND_THREAD_ID}))
        )
        if subscribed:
            subscription = await create_hook_subscription(
                database,
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                actor_type="user",
                actor_id=USER_ID,
                subscription=CreateHookSubscriptionRequest(
                    hook_names=("run.accepted",),
                    session_id=SESSION_ID,
                    webhook=WebhookDestinationConfig(
                        endpoint_url="https://hooks.example.com/foundation", signing_secret_id=SECRET_ID
                    ),
                ),
                now=NOW,
            )
            revision_id = subscription.current_revision_id

    ready = {run_id: anyio.Event() for run_id in (RUN_ID, SECOND_RUN_ID)}

    async def synchronize_after_event_insert(database: AsyncSession, event: LifecycleEventRecord) -> None:
        # LifecycleWriter has already flushed the event and acquired its Session
        # FK key-share lock. Hold both transactions here to force the old upgrade
        # deadlock instead of relying on scheduler timing. Production has no wait.
        ready[event.run_id].set()
        for inserted in ready.values():
            await inserted.wait()

    writer = LifecycleWriter((synchronize_after_event_insert, write_hook_lifecycle))

    async def append(run_id: str) -> None:
        async with transaction(sessions) as database:
            run = await database.get(RunRecord, run_id, with_for_update=True)
            assert run is not None
            await writer.append_run_lifecycle(
                database, run, "run.accepted", occurred_at=NOW, actor_type="user", actor_id=USER_ID
            )

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(append, RUN_ID)
            tasks.start_soon(append, SECOND_RUN_ID)

    async with short_session(sessions) as database:
        events = tuple(await database.scalars(select(LifecycleEventRecord)))
        deliveries = tuple(await database.scalars(select(OutboxRecord)))
        assert {event.run_id for event in events} == {RUN_ID, SECOND_RUN_ID}
        assert len(events) == 2 and all(event.resource_seq == 1 for event in events)
        assert len(deliveries) == (2 if subscribed else 0)
        if subscribed:
            assert {delivery.source_id for delivery in deliveries} == {event.id for event in events}
            assert all(
                delivery.destination_ref == revision_id and delivery.status == "pending" for delivery in deliveries
            )

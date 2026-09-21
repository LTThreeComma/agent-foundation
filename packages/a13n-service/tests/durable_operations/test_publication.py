"""Drain stops claimed-but-not-dispatched work without changing durable authority."""

from datetime import timedelta

import anyio
import pytest
from a13n_service.background import Sweep
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import complete_outbox
from a13n_service.durable_operations.publication import dispatch_outbox_batch
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from tests.durable_operations.test_outbox_transitions import _claim, _record
from tests.interactions.conftest import NOW
from tests.interactions.conftest import interaction_sessions as interaction_sessions

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("drain_before_dispatch", [False, True])
async def test_drain_preserves_undispatched_claims_for_ordinary_recovery(interaction_sessions, drain_before_dispatch):
    sessions = interaction_sessions
    async with transaction(sessions) as database:
        database.add_all([_record(str(index)) for index in range(3)])
    async with transaction(sessions) as database:
        claims = await _claim(database)
    draining = anyio.Event()
    if drain_before_dispatch:
        draining.set()
    published = []

    async def publish(claim):
        published.append(claim.outbox_id)
        draining.set()
        # An already-entered effect may finish while queued dispatches must not start.
        await anyio.sleep(0)
        async with transaction(sessions) as database:
            assert await complete_outbox(database, claim, completed_at=NOW)

    result = await dispatch_outbox_batch(
        sessions,
        claims,
        publish,
        timeout_seconds=1,
        concurrency=1,
        clock=lambda: NOW,
        is_draining=draining.is_set,
    )
    completed = int(not drain_before_dispatch)
    assert len(published) == completed
    assert result == Sweep(examined=3, completed=completed, deferred=3 - completed, oldest_age_seconds=0)
    async with short_session(sessions) as database:
        remaining = tuple(await database.scalars(select(OutboxRecord).where(OutboxRecord.status == "publishing")))
        assert len(remaining) == 3 - completed
        assert all(row.claim_generation == row.attempt_count == 1 for row in remaining)
    later = NOW + timedelta(seconds=31)
    async with transaction(sessions) as database:
        recovered = await _claim(database, now=later)
        assert len(recovered) == 3 - completed
        for claim in recovered:
            assert claim.generation == claim.attempt_count == 2
            stale = next(item for item in claims if item.outbox_id == claim.outbox_id)
            assert not await complete_outbox(database, stale, completed_at=later)
            assert await complete_outbox(database, claim, completed_at=later)

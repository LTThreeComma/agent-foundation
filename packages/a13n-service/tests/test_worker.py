"""Worker supervision: confirmed leases, independent groups, and task lifecycle boundaries."""

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest
from a13n_service.runs import worker as worker_module
from a13n_service.runs.attempts import AttemptControl, Lease
from a13n_service.runs.renewals import Renewal
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.worker import Worker

pytestmark = pytest.mark.anyio

LEASE = Lease(
    run_id="run_test",
    attempt_id="rat_test",
    thread_id="thread_test",
    organization_id="org_test",
    workspace_id="ws_test",
    number=1,
    worker_id="worker-test",
    token="token",
)


def _with_worker(runtime: Runtime, **worker: object) -> Runtime:
    settings = runtime.settings
    return replace(runtime, settings=settings.model_copy(update={"worker": settings.worker.model_copy(update=worker)}))


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(10):
        while not condition():
            await asyncio.sleep(0.01)


async def test_a_claim_failure_keeps_the_worker_and_its_running_attempts(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Renewal is not due within the test, so only the claim loop is exercised.
    runtime = _with_worker(runtime, scan_seconds=0.01, authority_seconds=5)
    claims = 0

    async def flaky_claim(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        nonlocal claims
        claims += 1
        if claims == 2:
            raise ConnectionError("database restarted")
        return [LEASE] if claims == 1 else []

    started, finish = asyncio.Event(), asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        started.set()
        await finish.wait()

    monkeypatch.setattr(worker_module, "claim", flaky_claim)
    worker = Worker(runtime, attempt)
    loop = asyncio.create_task(worker.run())
    await started.wait()
    await _until(lambda: claims >= 4)
    assert not loop.done() and LEASE.attempt_id in worker.running

    finish.set()
    await _until(lambda: not worker.running)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop


async def test_a_renewal_that_never_answers_stops_the_attempt(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, lease_seconds=3, authority_seconds=0.01, renewal_timeout=0.1, scan_seconds=5)
    durations: list[float] = []

    async def hanging_renew(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        sent = loop.time()
        try:
            await asyncio.Event().wait()
        finally:
            durations.append(loop.time() - sent)

    unclaimed = [LEASE]

    async def claim_once(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [unclaimed.pop()] if unclaimed else []

    stopped = asyncio.Event()

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            stopped.set()
            raise

    monkeypatch.setattr(worker_module, "claim", claim_once)
    monkeypatch.setattr(worker_module, "renew", hanging_renew)
    worker = Worker(runtime, attempt)
    loop = asyncio.create_task(worker.run())
    # The lease can no longer be renewed in time once a third of it is left: the attempt stops then.
    async with asyncio.timeout(5):
        await stopped.wait()
    await _until(lambda: not worker.running)
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop
    assert len(durations) >= 2
    assert min(durations) >= runtime.settings.worker.renewal_timeout * 0.9


async def test_every_supervision_renews_but_only_confirmation_advances_the_deadline(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, authority_seconds=0.01, scan_seconds=5)
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    controls: list[AttemptControl] = []
    initial = 0.0
    calls = 0
    confirmed: float | None = None
    dispatched: float | None = None

    unclaimed = [LEASE]

    async def claim_once(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [unclaimed.pop()] if unclaimed else []

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        nonlocal initial
        controls.append(control)
        initial = control.deadline
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def renewing(storage, access, leases, *, seconds) -> dict[str, Renewal]:  # type: ignore[no-untyped-def]
        nonlocal calls, confirmed, dispatched
        assert leases == [LEASE]
        control = controls[0]
        calls += 1
        assert seconds == 30  # Renew even while more than two thirds of the lease remain.
        assert not stopped.is_set()
        if calls == 1:
            dispatched = loop.time()
            await asyncio.sleep(0.01)
            assert control.deadline == initial  # An unconfirmed transaction grants no extra time.
            return {LEASE.attempt_id: Renewal("renewed")}
        if calls == 2:
            assert dispatched is not None
            confirmed = control.deadline
            assert initial < confirmed <= dispatched + seconds
            raise ConnectionError("database restarted")
        assert control.deadline == confirmed  # A failed renewal keeps the last confirmed deadline.
        return {LEASE.attempt_id: Renewal("lost")}

    monkeypatch.setattr(worker_module, "claim", claim_once)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    running = asyncio.create_task(worker.run())
    try:
        async with asyncio.timeout(5):
            await stopped.wait()
            await _until(lambda: not worker.running)
        assert calls == 3
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("slots", [4, 65, 128])
async def test_groups_are_bounded_stable_and_renew_independently(runtime, monkeypatch, slots: int) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, slots=slots, authority_seconds=0.01, renewal_timeout=5, scan_seconds=0.01)
    leases = [replace(LEASE, attempt_id=f"rat_{n}") for n in range(slots)]
    replacement = replace(LEASE, attempt_id="rat_replacement")
    pending = list(leases)
    finishes = {lease.attempt_id: asyncio.Event() for lease in [*leases, replacement]}
    blocked, release = asyncio.Event(), asyncio.Event()
    calls: list[set[str]] = []

    async def claiming(*args, limit, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        claimed = pending[:limit]
        del pending[:limit]
        return claimed

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        await finishes[lease.attempt_id].wait()

    async def renewing(storage, access, batch, *, seconds) -> dict[str, Renewal]:  # type: ignore[no-untyped-def]
        ids = {lease.attempt_id for lease in batch}
        assert 0 < len(ids) <= 64
        calls.append(ids)
        if leases[0].attempt_id in ids and slots > 64:
            blocked.set()
            await release.wait()
        return {key: Renewal("renewed") for key in ids}

    monkeypatch.setattr(worker_module, "claim", claiming)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    running = asyncio.create_task(worker.run())
    try:
        await _until(lambda: len(worker.running) == slots)
        original = {key: index for index, members in enumerate(worker._groups) for key in members}
        assert len(worker._groups) == (slots + 63) // 64
        assert max(map(len, worker._groups)) <= 64
        if slots > 64:
            await blocked.wait()
            await _until(lambda: len(calls) >= 4)
            # One group's transaction is still pending while the other confirms several renewals.
            assert sum(leases[0].attempt_id in batch for batch in calls) == 1
            victim = next(key for key, index in original.items() if index != original[leases[0].attempt_id])
        else:
            await _until(lambda: len(calls) >= 2)
            victim = leases[-1].attempt_id
        pending.append(replacement)
        finishes[victim].set()
        await _until(lambda: replacement.attempt_id in worker.running)
        current = {key: index for index, members in enumerate(worker._groups) for key in members}
        assert current[replacement.attempt_id] == original[victim]
        assert all(current[key] == index for key, index in original.items() if key != victim)
    finally:
        release.set()
        for finish in finishes.values():
            finish.set()
        await _until(lambda: not worker.running)
        count = len(calls)
        await asyncio.sleep(0.03)
        assert len(calls) == count  # Empty groups send no renewals.
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_skipped_and_lost_results_stop_only_their_attempts(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, slots=3, authority_seconds=0.01, scan_seconds=5)
    leases = [replace(LEASE, attempt_id=f"rat_{n}") for n in range(3)]
    pending = list(leases)
    controls: dict[str, AttemptControl] = {}
    cancelled: set[str] = set()
    finish = asyncio.Event()
    calls = 0
    skipped_deadline = 0.0

    async def claiming(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        claimed = list(pending)
        pending.clear()
        return claimed

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        controls[lease.attempt_id] = control
        try:
            await finish.wait()
        except asyncio.CancelledError:
            cancelled.add(lease.attempt_id)
            raise

    async def renewing(storage, access, batch, *, seconds) -> dict[str, Renewal]:  # type: ignore[no-untyped-def]
        nonlocal calls, skipped_deadline
        calls += 1
        if calls == 1:
            skipped_deadline = controls[leases[1].attempt_id].deadline
        elif calls == 2:
            assert controls[leases[1].attempt_id].deadline == skipped_deadline
            assert leases[2].attempt_id in cancelled
            # Only the skipped attempt reaches its safety margin; healthy results keep their own deadline.
            controls[leases[1].attempt_id].deadline = asyncio.get_running_loop().time()
        return {
            leases[0].attempt_id: Renewal("renewed"),
            leases[1].attempt_id: Renewal("skipped"),
            leases[2].attempt_id: Renewal("lost"),
        }

    monkeypatch.setattr(worker_module, "claim", claiming)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    running = asyncio.create_task(worker.run())
    try:
        await _until(lambda: len(cancelled) == 2)
        assert cancelled == {lease.attempt_id for lease in leases[1:]}
        assert set(worker.running) == {leases[0].attempt_id}
        assert not controls[leases[0].attempt_id].renewal_missed()
    finally:
        finish.set()
        await _until(lambda: not worker.running)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_renewals_continue_while_stopping_and_draining(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, slots=1, authority_seconds=0.01, scan_seconds=5, drain_seconds=5)
    pending = [LEASE]
    controls: list[AttemptControl] = []
    finish = asyncio.Event()
    calls = 0

    async def claiming(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [pending.pop()] if pending else []

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        controls.append(control)
        await finish.wait()

    async def renewing(*args, **kwargs) -> dict[str, Renewal]:  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {LEASE.attempt_id: Renewal("renewed", Outcome.cancelled())}

    monkeypatch.setattr(worker_module, "claim", claiming)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    running = asyncio.create_task(worker.run())
    try:
        await _until(lambda: bool(controls) and controls[0].stopped.is_set())
        running.cancel()
        await _until(lambda: controls[0].handoff.is_set())
        count = calls
        await _until(lambda: calls >= count + 2)
        assert LEASE.attempt_id in worker.running and not running.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await running
    finally:
        finish.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_a_late_result_cannot_update_an_exited_attempt(runtime, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runtime = _with_worker(runtime, slots=1, authority_seconds=0.01, scan_seconds=0.01, renewal_timeout=5)
    pending = [LEASE]
    entered, release, finish, returned = (asyncio.Event() for _ in range(4))
    controls: list[AttemptControl] = []

    async def claiming(*args, **kwargs) -> list[Lease]:  # type: ignore[no-untyped-def]
        return [pending.pop()] if pending else []

    async def attempt(runtime, lease, control) -> None:  # type: ignore[no-untyped-def]
        controls.append(control)
        await finish.wait()

    async def renewing(*args, **kwargs) -> dict[str, Renewal]:  # type: ignore[no-untyped-def]
        entered.set()
        await release.wait()
        returned.set()
        return {LEASE.attempt_id: Renewal("renewed", Outcome.cancelled())}

    monkeypatch.setattr(worker_module, "claim", claiming)
    monkeypatch.setattr(worker_module, "renew", renewing)
    worker = Worker(runtime, attempt)
    running = asyncio.create_task(worker.run())
    try:
        await entered.wait()
        deadline = controls[0].deadline
        finish.set()
        await _until(lambda: not worker.running)
        release.set()
        await returned.wait()
        assert controls[0].deadline == deadline and not controls[0].stopped.is_set()
    finally:
        finish.set()
        release.set()
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

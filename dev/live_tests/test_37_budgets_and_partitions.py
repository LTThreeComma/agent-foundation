"""Accepted budgets and role-specific dependency loss survive process replacement."""

import signal

import anyio
import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "recovery", [{"environment": {"A13N_SERVICE_GATEWAY_RUN_EXECUTION_MAX_ATTEMPTS": "2"}}], indirect=True
)
async def test_repeated_worker_crash_exhausts_the_accepted_attempt_budget(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("gate")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "gate_ready", run_id=run_id)
    budget = (await journey.snapshot(run_id))["run"]["execution_budget"]
    for index in range(2):
        await lab.stop(lab.workers[-1], signal.SIGKILL)
        await journey.restart_control()
        await lab.start_worker()
        if index == 0:
            await live.wait(
                lambda: live.evidence(case),
                lambda value: value["model_requests"] == 2,
                "second attempt reached the model",
            )
    result = await journey.sealed_consistently(run_id, "failed")
    assert result["failure"]["code"] == "execution_attempts_exhausted"
    persisted = (await journey.snapshot(run_id))["run"]
    assert persisted["execution_budget"] == budget
    assert persisted["attempts_charged"] == 2 and persisted["handoffs_completed"] == 0
    assert len((await journey.snapshot(run_id))["attempts"]) == 2
    assert (await live.evidence(case))["model_requests"] == 2
    await live.release(case)
    await lab.start_worker()
    await live.assert_stable(lambda: live.run(run_id), result, seconds=1)


@pytest.mark.parametrize("recovery", [{"policy": {"deadline_seconds": 6}}], indirect=True)
async def test_accepted_deadline_does_not_restart_with_control_or_worker(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    await lab.stop(lab.workers[0], signal.SIGKILL)
    case = await live.case("basic")
    receipt = await live.start(case)
    before = await journey.snapshot(receipt["run_id"])
    await journey.restart_control()
    await anyio.sleep(6)
    await lab.start_worker()
    result = await journey.sealed_consistently(receipt["run_id"], "failed")
    assert result["failure"]["code"] == "execution_deadline_exhausted"
    assert (await journey.snapshot(receipt["run_id"]))["run"]["execution_budget"] == before["run"]["execution_budget"]
    assert (await live.evidence(case))["model_requests"] == 0
    assert (await journey.snapshot(receipt["run_id"]))["attempts"] == []


@pytest.mark.parametrize("recovery", [{"policy": {"max_usage": {"model_requests": 1}}}], indirect=True)
async def test_model_request_budget_is_charged_once_and_retained_on_recovery(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    agent = await journey.agent(plugins=journey.recovery_plugins())
    case = await live.case("recovery_inbox")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run_id = receipt["run_id"]
    await journey.marker(case, "inbox_ready")
    before = await journey.snapshot(run_id)
    assert before["attempts"][0]["usage"]["model_requests"] == 1
    await lab.stop(lab.workers[0], signal.SIGKILL)
    await journey.restart_control()
    journey.release_marker(case, "inbox_release")
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id, "failed")
    persisted = (await journey.snapshot(run_id))["run"]
    assert persisted["execution_budget"] == before["run"]["execution_budget"]
    assert persisted["usage_charged"]["model_requests"] == 1
    assert (await live.evidence(case))["model_requests"] == 1
    assert len(journey.model_observations(case)) == 1
    snapshot = await journey.snapshot(run_id)
    assert sum(item["usage"]["model_requests"] for item in snapshot["attempts"]) == 1
    await journey.restart_control()
    assert await live.run(run_id) == result


@pytest.mark.parametrize("dependency", ["postgres", "redis", "objects"])
@pytest.mark.parametrize("roles", ["control", "worker", "both"])
async def test_role_partition_cannot_change_already_committed_execution_authority(recovery, dependency, roles):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    before = await journey.snapshot(run_id)
    proxies = [
        lab.proxies[name]
        for name in (
            ["control." + dependency]
            if roles == "control"
            else [dependency]
            if roles == "worker"
            else [dependency, "control." + dependency]
        )
    ]
    for proxy in proxies:
        proxy.cut()
    try:
        # The upstream model has its own process and no failed Control dependency.
        (journey.case_root(case) / "release").touch()
        with anyio.fail_after(30):
            while not all(proxy.rejected for proxy in proxies):
                # Object storage is otherwise idle on Control until replay. A
                # bounded request exercises that surface without holding SQL.
                if dependency == "objects" and roles in {"control", "both"}:
                    async with journey.pending(live.events(run_id)):
                        await anyio.sleep(0.1)
                else:
                    await anyio.sleep(0.1)
        during = await journey.snapshot(run_id)
        affected = (
            lab.controls if roles == "control" else lab.workers if roles == "worker" else [*lab.controls, *lab.workers]
        )
        # /readyz probes SQL and Redis. Object availability is checked by the
        # actual rejected operations above, not by a readiness probe it lacks.
        if dependency != "objects":
            for process in affected:
                await lab.wait_unready(process)
        assert during["run"]["execution_budget"] == before["run"]["execution_budget"]
        if roles in {"worker", "both"} and dependency in {"postgres", "objects"}:
            assert during["run"]["status"] != "completed"
        await lab.stop(lab.workers[0], signal.SIGKILL)
        for control in lab.controls:
            if control.returncode is None:
                await lab.stop(control, signal.SIGKILL)
    finally:
        for proxy in proxies:
            proxy.restore()
    await lab.start_control()
    await lab.start_worker()
    result = await live.wait(
        lambda: live.run(run_id), lambda value: value["status"] in {"completed", "failed"}, "partition converged"
    )
    if result["status"] == "completed":
        await journey.sealed_consistently(run_id)
        assert result["output_text"] == case["token"]
    else:
        # Worker publication failure may terminalize before the parent kills it.
        assert result["failure"]["code"] == "attempt_dependency_unavailable"
        assert result["sealed_state_digest_sha256"] is None
    assert (await live.evidence(case))["effects"] == 1
    snapshot = await journey.snapshot(run_id)
    assert all(attempt["status"] in {"failed", "succeeded"} for attempt in snapshot["attempts"])
    assert len({attempt["id"] for attempt in snapshot["attempts"]}) == len(snapshot["attempts"])
    probe = await live.start(await live.case("basic"))
    await journey.sealed_consistently(probe["run_id"])
    assert await live.run(run_id) == result


@pytest.mark.parametrize(
    "recovery",
    [
        {
            "environment": {
                "A13N_SERVICE_GATEWAY_RUN_MAX_HANDOFFS": "1",
                "A13N_SERVICE_WORKER_DRAIN_SECONDS": "120",
            }
        }
    ],
    indirect=True,
)
async def test_planned_handoff_allowance_is_not_replenished_by_restart(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    agent = await journey.agent(plugins=journey.recovery_plugins())
    case = await live.case("recovery_handoff")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run_id = receipt["run_id"]
    await journey.marker(case, "handoff_ready_1")
    lab.send(lab.workers[0], signal.SIGTERM)
    await lab.wait_unready(lab.workers[0])
    journey.release_marker(case, "handoff_release_1")
    first = await live.wait(
        lambda: journey.snapshot(run_id),
        lambda value: value["attempts"][0]["status"] == "yielded",
        "first planned handoff",
    )
    assert first["run"]["handoffs_completed"] == 1
    assert first["run"]["attempts_charged"] == 1
    await journey.restart_control()
    second = await lab.start_worker()
    await journey.marker(case, "handoff_ready_2")
    lab.send(second, signal.SIGTERM)
    await lab.wait_unready(second)
    journey.release_marker(case, "handoff_release_2")
    result = await journey.sealed_consistently(run_id)
    assert result["output_text"] == "handoffs-finished"
    after = await journey.snapshot(run_id)
    assert [attempt["status"] for attempt in after["attempts"]] == ["yielded", "succeeded"]
    assert after["run"]["handoffs_completed"] == 1 and after["run"]["attempts_charged"] == 1
    assert after["run"]["execution_budget"] == first["run"]["execution_budget"]
    assert (await live.evidence(case))["model_requests"] == 3

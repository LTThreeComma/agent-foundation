"""Durable queue and Worker-owned projection survive crashes and competing scans."""

import signal

import pytest
from a13n_service.run_stream import RedisRunStream
from a13n_service.run_stream.domain import RunStreamReplayGap
from a13n_service.run_stream.redis import _keys

from .stream import assert_stream
from .test_34_fences_and_handoff import enqueue, finish_queue

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("window", ["before_publication", "lost_redis_ack", "before_sql_ack"])
async def test_committed_terminal_fact_survives_publisher_crash_and_replicas(recovery, window):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    point = {
        "before_publication": "projection.before",
        "lost_redis_ack": "redis.mutate.after",
        "before_sql_ack": "projection.after",
    }[window]
    fault = journey.arm(point, where={"run_id": run_id, "event_type": "run.completed"})
    await live.release(case)
    await journey.kill_at(fault)
    snapshot = await journey.snapshot(run_id)
    assert snapshot["run"]["status"] == "completed"
    terminal = next(event for event in snapshot["events"] if event["event_type"] == "run.completed")
    assert terminal["projection_state"] == "projecting"
    if window != "before_publication":
        page = await RedisRunStream(journey.redis).read(
            live.config["organization_id"], run_id, after_stream_id=None, limit=1000
        )
        assert sum(entry.event.event_type == "run.completed" for entry in page.items) == 1
    await journey.restart_control()
    await lab.start_control(replica=True)
    await lab.start_worker()
    await lab.start_worker()
    await live.wait(
        lambda: journey.snapshot(run_id),
        lambda value: all(event["projection_state"] == "projected" for event in value["events"]),
        "durable publication reclaimed by a Worker projector",
    )
    result = await journey.sealed_consistently(run_id)
    events = await live.events(run_id)
    assert_stream(events, run_id)
    assert next(event.data["lifecycle_event_id"] for event in events if event.kind == "run.completed") == terminal["id"]
    assert (await live.evidence(case))["effects"] == 1
    assert (await live.evidence(case))["model_requests"] == 2
    await journey.restart_control()
    assert await live.run(run_id) == result
    assert await live.events(run_id) == events


async def test_partial_redis_activation_is_repaired_after_worker_and_control_crash(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    publisher_gate = journey.arm("projection.before", where={"event_type": "run_attempt.leased"})
    partial = journey.arm("redis.partial", where={"operation": "activate"})
    case = await live.case("basic")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await partial.reached()
    await publisher_gate.reached()
    with pytest.raises(RunStreamReplayGap):
        await RedisRunStream(journey.redis).read(
            live.config["organization_id"], run_id, after_stream_id=None, limit=1000
        )
    # The public reader must reject incomplete publication; independent raw
    # storage evidence proves that the Lua failure followed an actual write.
    stream_key, metadata_key = _keys(live.config["organization_id"], run_id)
    assert await journey.redis.xlen(stream_key) > 1
    assert await journey.redis.hget(metadata_key, "pending") is not None
    assert (await live.evidence(case))["model_requests"] == 0
    await journey.kill_at(partial)
    await lab.stop(lab.controls[0], signal.SIGKILL)
    await lab.start_control()
    await lab.start_control(replica=True)
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id)
    assert result["output_text"] == case["token"]
    assert (await live.evidence(case))["model_requests"] == 1
    events = await live.events(run_id)
    assert_stream(events, run_id)
    assert sum(event.kind == "run.accepted" for event in events) == 1
    assert sum(event.kind == "run_attempt.leased" for event in events) == 2
    assert sum(event.kind == "run.recovery" for event in events) == 1


async def test_queue_recovery_can_crash_twice_then_overlap_without_duplicate_consumption(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("gate")
    source = await live.start(case)
    await lab.wait_evidence(case, "gate_ready", run_id=source["run_id"])
    cases = [await live.case("basic") for _ in range(2)]
    queued = [await enqueue(journey, source, later) for later in cases]
    # Force candidate adoption, whose queue continuation belongs to the real
    # Control recovery scanner rather than the executing Worker's combined commit.
    candidate = journey.arm("state.replace.after", where={"run_id": source["run_id"], "kind": "completed"})
    await live.release(case)
    await journey.kill_at(candidate)
    recovering = journey.arm("acceptance.commit.before", role="control", hits=2)
    await lab.start_worker()
    for index in range(2):
        await journey.kill_at(recovering, index=index)
        snapshot = await journey.snapshot(source["run_id"])
        assert all(item["state"] == "queued" for item in snapshot["queue"])
        await lab.start_control()
    await lab.start_control(replica=True)
    runs = await finish_queue(journey, source, queued, cases)
    await journey.restart_control()
    assert await live.collection(f"/api/v1/threads/{source['thread_id']}/runs") == runs
    assert (await live.evidence(case))["model_requests"] == 1


@pytest.mark.parametrize("dependency", ["postgres", "redis"])
async def test_dependency_process_restart_preserves_committed_run_state(recovery, dependency):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    # End the current owner so restart cannot depend on its in-memory state.
    await lab.stop(lab.workers[0], signal.SIGKILL)
    before = await journey.state(run_id)
    calls = (await live.evidence(case))["model_requests"]
    await journey.restart_dependency(dependency)
    assert (await journey.state(run_id)).body == before.body
    await live.release(case)
    await lab.start_worker()
    if dependency == "postgres":
        result = await journey.sealed_consistently(run_id)
        assert result["output_text"] == case["token"]
        assert_stream(await live.events(run_id), run_id)
    else:
        # A different Redis incarnation cannot safely continue the already
        # projected activation, even when RDB retained its bytes.
        result = await journey.sealed_consistently(run_id, "failed")
        assert result["failure"]["code"] == "attempt_dependency_unavailable"
        assert result["sealed_state_digest_sha256"] is None
        assert (await journey.state(run_id)).body == before.body
        assert (await live.evidence(case))["model_requests"] == calls
        await assert_replay_gap(live, run_id)
    assert (await live.evidence(case))["effects"] == 1


async def test_lost_redis_presentation_never_reconstructs_history_from_agent_state(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    gate = journey.arm("projection.before", where={"event_type": "run.completed"})
    case = await live.case("basic")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    # Stop projection before terminal snapshot construction, so there is no
    # complete object replay to legitimately serve after Redis data loss.
    await gate.reached()
    await journey.kill_at(gate)
    state = await journey.state(run_id)
    before = await journey.snapshot(run_id)
    await journey.restart_dependency("redis", lose_redis=True)
    await journey.restart_control()
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id)
    assert result["sealed_state_digest_sha256"] == state.digest_sha256
    assert (await journey.snapshot(run_id))["run"] == before["run"]
    await assert_replay_gap(live, run_id)
    probe = await live.start(await live.case("basic"))
    await journey.sealed_consistently(probe["run_id"])
    assert (await live.evidence(case))["model_requests"] == 1


async def assert_replay_gap(live, run_id):
    async with live.http.stream(
        "GET", f"/api/v1/runs/{run_id}/stream", headers={"Accept": "text/event-stream"}
    ) as response:
        if response.status_code == 409:
            import json

            body = json.loads(await response.aread())
            assert body["error"]["code"] == "run_stream_replay_gap"
        else:
            from .stream import parse_events

            assert response.status_code == 200
            with pytest.raises(AssertionError, match="Run Stream reported a replay gap"):
                async for _ in parse_events(response.aiter_lines()):
                    pass

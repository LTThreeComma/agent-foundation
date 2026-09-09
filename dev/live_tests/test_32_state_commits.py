"""Exact acceptance, outcome, and unknown-write windows across real stores."""

import signal
from uuid import uuid4

import httpx2
import pytest

from .management_support import client_tool, feedback_body

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("point", ["state.create.after", "acceptance.commit.before"])
@pytest.mark.parametrize("recovery", [{"collection_seconds": 1}], indirect=True)
async def test_initial_objects_without_relational_acceptance_are_not_runs(recovery, point):
    journey, live, lab = recovery, recovery.live, recovery.lab
    await lab.stop(lab.workers[0])
    case = await live.case("basic")
    before = await journey.runs()
    key = uuid4().hex
    fault = journey.arm(point, role="control")
    async with journey.pending(live.start(case, key=key)) as request:
        evidence = await journey.kill_at(fault)
        with pytest.raises(httpx2.TransportError):
            await request
    orphan_id = evidence.get("run_id") or evidence["run_ids"][0]
    assert await journey.snapshot(orphan_id) is None
    orphan = await journey.state(orphan_id)
    assert orphan.envelope.checkpoint_seq == 0
    await lab.start_control()
    assert await journey.runs() == before, "Rolled-back acceptance left a visible Run"
    accepted = await live.start(case, key=key)
    assert await live.start(case, key=key) == accepted
    assert accepted["run_id"] != orphan_id
    await lab.start_worker()
    result = await journey.sealed_consistently(accepted["run_id"])
    assert result["output_text"] == case["token"]
    assert (await live.evidence(case))["model_requests"] == 1
    assert await journey.snapshot(orphan_id) is None
    await live.wait(lambda: journey.exists(orphan.info.key), lambda value: not value, "unaccepted state collected")


async def test_committed_acceptance_survives_lost_response_and_control_restart(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    await lab.stop(lab.workers[0])
    case, key = await live.case("basic"), uuid4().hex
    fault = journey.arm("acceptance.commit.after", role="control")
    async with journey.pending(live.start(case, key=key)) as request:
        evidence = await journey.kill_at(fault)
        with pytest.raises(httpx2.TransportError):
            await request
    run_id = evidence["run_ids"][0]
    snapshot = await journey.snapshot(run_id)
    assert snapshot["run"]["status"] == "accepted"
    assert snapshot["thread"]["current_run_id"] == run_id
    assert [event["event_type"] for event in snapshot["events"]] == ["run.accepted"]
    await lab.start_control()
    receipt = await live.start(case, key=key)
    assert receipt["run_id"] == run_id
    await live.request(
        "POST",
        journey.base + "/runs",
        expected=409,
        headers={"Idempotency-Key": key},
        json={
            **live.start_body(case),
            "input": {"schema_version": "2", "content": [{"type": "text", "text": "changed intent"}]},
        },
    )
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id)
    assert await live.start(case, key=key) == receipt
    assert (await live.evidence(case))["model_requests"] == 1
    assert await live.run(run_id) == result


@pytest.mark.parametrize("outcome", ["completed", "waiting"])
async def test_outcome_object_survives_crash_before_seal_without_reexecution(recovery, outcome):
    journey, live, lab = recovery, recovery.live, recovery.lab
    if outcome == "waiting":
        agent = await journey.agent(client_tools=[client_tool()])
        case = await journey.case(steps=[{"tool": "live_client", "arguments": {"prompt": "Supply a value"}}])
        options = {"agent_id": agent["agent"]["id"]}
    else:
        case, options = await live.case("checkpoint"), {}
        await live.release(case)
    fault = journey.arm("state.replace.after", where={"kind": outcome})
    receipt = await journey.start(case, **options)
    run_id = receipt["run_id"]
    evidence = await journey.kill_at(fault)
    assert evidence["run_id"] == run_id
    before = await journey.snapshot(run_id)
    prepared = await journey.state(run_id)
    assert before["run"]["status"] == "running" and before["run"]["sealed_state"] is None
    assert before["thread"]["head_run_id"] is None
    assert prepared.envelope.outcome_candidate is not None
    calls = (await live.evidence(case))["model_requests"]
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id, outcome)
    after = await journey.snapshot(run_id)
    assert len(after["attempts"]) == 2
    assert after["attempts"][1]["harness_run_id"] is None, "Candidate adoption unexpectedly re-entered Harness"
    assert result["sealed_state_digest_sha256"] == prepared.digest_sha256
    assert (await live.evidence(case))["model_requests"] == calls
    if outcome == "completed":
        assert result["output_text"] == case["token"]
        assert (await live.evidence(case))["effects"] == 1
    else:
        body = await feedback_body(live, result, {"value": "recovered-client-value"})
        successor = await journey.post(f"/api/v1/runs/{run_id}/feedback", body, expected=202)
        live.track(successor)
        assert "recovered-client-value" in (await live.finish(successor["run_id"]))["output_text"]
    await journey.restart_control()
    assert await live.run(run_id) == result


@pytest.mark.parametrize("read_fails", [False, True])
async def test_committed_object_with_lost_ack_is_reconciled_exactly(recovery, read_fails):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    fault = journey.arm("objects.put.after", where={"run_id": run_id, "kind": "completed"}, action="timeout")
    await live.release(case)
    evidence = await fault.reached()
    stored = await journey.state(run_id)
    assert stored.info.version == evidence["version"]
    assert (await journey.snapshot(run_id))["run"]["status"] == "running"
    if read_fails:
        read_fault = journey.arm("objects.stat.before", where={"key": stored.info.key}, action="unavailable")
    fault.release()
    if read_fails:
        await journey.kill_at(read_fault)
        await lab.start_worker()
    result = await journey.sealed_consistently(run_id)
    assert result["sealed_state_digest_sha256"] == stored.digest_sha256
    writes = journey.operations("objects.put.after", run_id=run_id)
    assert len([write for write in writes if write["kind"] == "completed" and write["fence"] == 1]) == 1
    assert (await live.evidence(case))["model_requests"] == 2
    assert (await live.evidence(case))["effects"] == 1


async def test_cancel_wins_over_an_inflight_completed_object(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    fault = journey.arm("objects.put.before", where={"run_id": run_id, "kind": "completed"}, shield=True)
    await live.release(case)
    await fault.reached()
    await live.interrupt(run_id)
    cancelled = await journey.sealed_consistently(run_id, "cancelled")
    assert (await journey.snapshot(run_id))["run"]["sealed_state"] is None
    fault.release()
    await live.wait(
        lambda: journey.state(run_id),
        lambda state: state.envelope.checkpoint_kind == "completed",
        "late prepared PUT reached storage",
    )
    await lab.stop(lab.workers[0], signal.SIGKILL)
    await lab.start_worker()
    await live.assert_stable(lambda: live.run(run_id), cancelled, seconds=2)
    assert len((await journey.snapshot(run_id))["attempts"]) == 1


async def test_accepted_steer_prevents_an_older_completion_candidate_from_sealing(recovery):
    from .client import agent_input

    journey, live = recovery, recovery.live
    agent = await journey.agent(plugins=journey.recovery_plugins())
    case = await live.case("recovery_inbox")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run_id = receipt["run_id"]
    await journey.marker(case, "inbox_ready")
    candidate = journey.arm("state.replace.after", where={"run_id": run_id, "kind": "completed"})
    journey.release_marker(case, "inbox_release")
    await candidate.reached()
    before = await journey.state(run_id)
    assert before.envelope.outcome_candidate is not None
    steer = await journey.post(
        f"/api/v1/runs/{run_id}/steer", agent_input("RECOVERY_STEER " + uuid4().hex), expected=202
    )
    assert (await journey.snapshot(run_id))["inbox"][0]["status"] == "pending"
    candidate.release()
    result = await journey.sealed_consistently(run_id)
    assert result["output_text"] == "received:1"
    assert result["sealed_state_digest_sha256"] != before.digest_sha256
    after = await journey.snapshot(run_id)
    assert after["inbox"][0]["id"] == steer["steer_id"]
    assert after["inbox"][0]["status"] == "consumed"
    consumed = after["inbox"][0]
    assert any(
        write["digest"] == consumed["consumed_state_digest_sha256"]
        and write["seq"] == consumed["consumed_checkpoint_seq"]
        and write["receipts"]
        for write in journey.operations("state.replace.after", run_id=run_id)
    )
    assert [item.inbox_entry_id for item in (await journey.state(run_id)).envelope.host.inbox_receipts] == [
        steer["steer_id"]
    ]
    assert len(after["attempts"]) == 2
    assert after["attempts"][1]["start_reason"] == "pending_input"
    assert (await live.evidence(case))["model_requests"] == 3
    await journey.restart_control()
    assert await live.run(run_id) == result

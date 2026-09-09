"""Force the SQL/object takeover race, queued handoff, and real drain commits."""

import signal

import anyio
import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("window", ["before_claim", "after_claim", "lost_ack_after_claim"])
async def test_late_old_writer_is_reconciled_across_relational_takeover(recovery, window):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    ack_lost = window == "lost_ack_after_claim"
    old_write = journey.arm(
        "objects.put.after" if ack_lost else "objects.put.before",
        where={"run_id": run_id, "kind": "completed", "fence": 1},
        action="timeout" if ack_lost else "pause",
        shield=True,
    )
    await live.release(case)
    old_hit = await old_write.reached()
    owner = next(process for process in lab.workers if process.pid == old_hit["pid"])
    lab.send(owner, signal.SIGSTOP)
    next_claim = journey.arm(
        "state.claim_writer.before" if window == "before_claim" else "state.claim_writer.after",
        where={"run_id": run_id, "claim_fence" if window == "before_claim" else "fence": 2},
    )
    await lab.start_worker()
    await next_claim.reached()
    selected = await journey.snapshot(run_id)
    assert len(selected["attempts"]) == 2 and selected["attempts"][0]["status"] == "failed"
    assert selected["run"]["current_run_attempt_id"] == selected["attempts"][1]["id"]
    prior = await journey.state(run_id)
    old_write.release()
    lab.send(owner, signal.SIGCONT)
    if window == "before_claim":
        current = await live.wait(
            lambda: journey.state(run_id),
            lambda value: value.envelope.checkpoint_kind == "completed",
            "old in-flight checkpoint won before new object claim",
        )
        assert current.writer_fence == 1 and current.info.version != prior.info.version
    else:
        assert prior.writer_fence == 2
        await lab.stop(owner, signal.SIGTERM)
        current = await journey.state(run_id)
        assert current.info.version == prior.info.version, "Stale writer changed the new owner's object"
    next_claim.release()
    result = await journey.sealed_consistently(run_id)
    after = await journey.snapshot(run_id)
    assert after["attempts"][0] == selected["attempts"][0]
    assert len(after["attempts"]) == 2 and result["output_text"] == case["token"]
    assert (await live.evidence(case))["effects"] == 1
    assert (await live.evidence(case))["model_requests"] == (3 if window == "after_claim" else 2)
    if owner.returncode is None:
        await lab.stop(owner, signal.SIGKILL)
    await live.assert_stable(lambda: live.run(run_id), result, seconds=1)


async def enqueue(journey, receipt, case):
    thread = await journey.live.thread(receipt["thread_id"])
    result = await journey.post(
        f"/api/v1/threads/{thread['id']}/runs",
        {"expected_thread_version": thread["version"], "input": journey.live.start_body(case)["input"]},
        expected=202,
    )
    assert result["outcome"] == "queued" and result["run"] is None
    return result["queued_submission"]


async def finish_queue(journey, source, submissions, cases):
    parent = await journey.live.finish(source["run_id"])
    for submission, case in zip(submissions, cases, strict=True):
        consumed = await journey.live.wait(
            lambda submission=submission: journey.live.request(
                "GET", f"/api/v1/queued-submissions/{submission['queued_submission_id']}"
            ),
            lambda value: value["state"] != "queued",
            "queued intent recovered",
        )
        assert consumed["state"] == "consumed"
        run_id = consumed["consumed_run_id"]
        journey.live.runs.append(run_id)
        result = await journey.live.finish(run_id)
        assert result["parent_run_id"] == parent["id"] and result["output_text"] == case["token"]
        assert (await journey.live.evidence(case))["model_requests"] == 1
        parent = result
    runs = await journey.live.collection(f"/api/v1/threads/{source['thread_id']}/runs")
    assert len(runs) == len(submissions) + 1
    thread = await journey.live.thread(source["thread_id"])
    assert thread["head_run_id"] == thread["current_run_id"] == parent["id"]
    return runs


async def test_prepared_queue_successor_is_not_accepted_when_combined_commit_rolls_back(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    case = await live.case("gate")
    source = await live.start(case)
    run_id = source["run_id"]
    await lab.wait_evidence(case, "gate_ready", run_id=run_id)
    cases = [await live.case("basic") for _ in range(2)]
    queued = [await enqueue(journey, source, later) for later in cases]
    before = await live.thread(source["thread_id"])
    fault = journey.arm("queue.commit.before", where={"run_id": run_id})
    await live.release(case)
    evidence = await journey.kill_at(fault)
    successor_id = next(value for value in evidence["run_ids"] if value != run_id)
    assert await journey.snapshot(successor_id) is None
    assert (await journey.state(successor_id)).envelope.checkpoint_seq == 0
    snapshot = await journey.snapshot(run_id)
    assert snapshot["run"]["status"] == "running"
    assert {key: snapshot["thread"][key] for key in before} == before
    assert all(row["state"] == "queued" and row["consumed_run_id"] is None for row in snapshot["queue"])
    assert (await journey.state(run_id)).envelope.checkpoint_kind == "completed"
    await lab.start_control(replica=True)
    await lab.start_worker()
    runs = await finish_queue(journey, source, queued, cases)
    assert successor_id not in {run["id"] for run in runs}
    assert (await live.evidence(case))["model_requests"] == 1
    after = await live.thread(source["thread_id"])
    assert after["version"] == before["version"] + 5
    assert after["queue_version"] == before["queue_version"] + 2
    await journey.restart_control()
    assert await live.collection(f"/api/v1/threads/{source['thread_id']}/runs") == runs


@pytest.mark.parametrize("recovery", [{"environment": {"A13N_SERVICE_WORKER_DRAIN_SECONDS": "120"}}], indirect=True)
@pytest.mark.parametrize("window", ["before_yield", "after_yield"])
async def test_checkpoint_and_yield_are_separate_durable_boundaries(recovery, window):
    journey, live, lab = recovery, recovery.live, recovery.lab
    agent = await journey.agent(plugins=journey.recovery_plugins())
    case = await live.case("recovery_inbox")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run_id = receipt["run_id"]
    await journey.marker(case, "inbox_ready")
    fault = journey.arm(
        "attempt.commit.before" if window == "before_yield" else "attempt.commit.after",
        where={"run_id": run_id, "attempt_statuses": ["yielded"]},
    )
    owner = lab.workers[0]
    owner.send_signal(signal.SIGTERM)
    await lab.wait_unready(owner)
    journey.release_marker(case, "inbox_release")
    await journey.kill_at(fault)
    state = await journey.state(run_id)
    assert state.envelope.checkpoint_kind == "progress"
    assert "recovery-step-complete" in state.envelope.harness.model_dump_json()
    before = await journey.snapshot(run_id)
    assert before["run"]["status"] == "running"
    assert before["run"]["handoffs_completed"] == int(window == "after_yield")
    assert before["attempts"][0]["status"] == ("yielded" if window == "after_yield" else "running")
    await lab.start_worker()
    if window == "before_yield":
        # A complete checkpoint alone must not release the still-current lease.
        await anyio.sleep(0.2)
        snapshot = await journey.snapshot(run_id)
        assert len(snapshot["attempts"]) == 1
    result = await journey.sealed_consistently(run_id)
    after = await journey.snapshot(run_id)
    assert result["output_text"] == "received:0"
    assert len(after["attempts"]) == 2
    assert after["attempts"][1]["start_reason"] == ("planned_handoff" if window == "after_yield" else "lease_expired")
    assert after["run"]["attempts_charged"] == (1 if window == "after_yield" else 2)
    assert (await live.evidence(case))["model_requests"] == 2

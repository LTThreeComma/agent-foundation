"""Recover SQL-pending inputs from complete, optionally compacted Host receipts."""

from uuid import uuid4

import pytest

from .client import agent_input

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("source", ["steer", "child"])
@pytest.mark.parametrize("compact", [False, True], ids=["full-history", "compacted-history"])
async def test_pending_inbox_is_repaired_from_state_without_reinjecting(recovery, source, compact):
    journey, live, lab = recovery, recovery.live, recovery.lab
    options = {"plugins": journey.recovery_plugins(compact=compact)}
    if source == "child":
        options.update(subagent_mode="async", subagents={"child": {"agent_id": live.config["child_agent_id"]}})
    agent = await journey.agent(**options)
    case = await live.case("recovery_child" if source == "child" else "recovery_inbox")
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    run_id = receipt["run_id"]
    await journey.marker(case, "inbox_ready")
    if source == "steer":
        batch = journey.arm("inbox.read.before", where={"run_id": run_id})
        await batch.reached()
        # Equal content with different identities must be consumed twice, once
        # per accepted entry; a replay of one identity must not add a third row.
        raw = "RECOVERY_STEER " + uuid4().hex
        entry_ids = []
        for _ in range(2):
            key = uuid4().hex
            steer = await journey.post(f"/api/v1/runs/{run_id}/steer", agent_input(raw), expected=202, key=key)
            assert await journey.post(f"/api/v1/runs/{run_id}/steer", agent_input(raw), expected=202, key=key) == steer
            entry_ids.append(steer["steer_id"])
        batch.release()
    else:
        await lab.start_worker()
        await journey.marker(case, "child_ready")
        journey.release_marker(case, "child_release")
        snapshot = await live.wait(
            lambda: journey.snapshot(run_id), lambda value: len(value["inbox"]) == 1, "child result durably accepted"
        )
        child_id = snapshot["inbox"][0]["payload"]["child_run_id"]
        live.runs.append(child_id)
        await live.finish(child_id)
        entry_ids = [snapshot["inbox"][0]["id"]]
        raw = "RECOVERY_CHILD_RESULT " + case["token"]
    snapshot = await journey.snapshot(run_id)
    assert [row["id"] for row in snapshot["inbox"]] == entry_ids
    assert all(row["status"] == "pending" for row in snapshot["inbox"])
    fault = journey.arm("state.replace.after", where={"run_id": run_id, "kind": "completed", "receipts": True})
    journey.release_marker(case, "inbox_release")
    await journey.kill_at(fault)
    stored = await journey.state(run_id)
    snapshot = await journey.snapshot(run_id)
    assert snapshot["run"]["status"] == "running" and snapshot["run"]["sealed_state"] is None
    assert all(row["status"] == "pending" and row["consumed_checkpoint_seq"] is None for row in snapshot["inbox"])
    assert [receipt.inbox_entry_id for receipt in stored.envelope.host.inbox_receipts] == entry_ids
    history = stored.envelope.harness.message_history_json.decode()
    assert (raw in history) is not compact
    observations = journey.model_observations(case)
    observed = [item for item in observations if item["inputs"]]
    assert observed, "Neither the model nor its compaction request observed the accepted inputs"
    assert all(item["inputs"] == [raw] * len(entry_ids) for item in observed)
    if compact:
        assert any(item["compacting"] and item["inputs"] for item in observations)
        assert observations[-1]["inputs"] == [], "Final ordinary request still contained raw inbox results"
        assert "RECOVERY_SUMMARY" in history
    calls = (await live.evidence(case))["model_requests"]
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id)
    assert result["output_text"] == f"received:{len(entry_ids)}"
    assert result["sealed_state_digest_sha256"] == stored.digest_sha256
    after = await journey.snapshot(run_id)
    for row in after["inbox"]:
        assert row["status"] == "consumed" and row["consumed_by_run_id"] == run_id
        assert row["consumed_state_digest_sha256"] == stored.digest_sha256
        assert row["consumed_checkpoint_seq"] == stored.envelope.checkpoint_seq
    assert len(after["attempts"]) == 2
    assert after["attempts"][-1]["harness_run_id"] is None
    assert (await live.evidence(case))["model_requests"] == calls
    assert journey.model_observations(case) == observations
    await journey.restart_control()
    assert (await journey.snapshot(run_id))["inbox"] == after["inbox"]
    assert await live.run(run_id) == result
    assert len(await live.collection(f"/api/v1/threads/{receipt['thread_id']}/runs")) == 1

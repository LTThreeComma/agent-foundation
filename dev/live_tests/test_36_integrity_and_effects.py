"""Corrupt selected state and lose effect acknowledgements in disposable stores."""

import hashlib
import json
import signal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("damage", ["missing", "truncated", "digest", "schema", "identity"])
async def test_invalid_state_fails_closed_before_any_model_or_tool(recovery, damage):
    journey, live, lab = recovery, recovery.live, recovery.lab
    await lab.stop(lab.workers[0], signal.SIGKILL)
    case = await live.case("basic")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    state = await journey.state(run_id)
    if damage == "missing":
        await journey.objects.delete(state.info.key, if_match=state.info.version)
    else:
        body, metadata = state.body, dict(state.info.metadata)
        if damage == "truncated":
            body = body[: len(body) // 2]
        elif damage == "digest":
            metadata["digest-sha256"] = "0" * 64
        else:
            value = json.loads(body)
            value["schema_version" if damage == "schema" else "run_id"] = (
                "unsupported" if damage == "schema" else "run_" + uuid4().hex
            )
            if damage == "identity":
                # Keep metadata internally consistent so rejection must check
                # the deterministic object key, not merely a stale digest/ID tag.
                metadata["run-id"] = value["run_id"]
            body = json.dumps(value, separators=(",", ":")).encode()
        if damage != "digest":
            metadata["digest-sha256"] = hashlib.sha256(body).hexdigest()
        await journey.objects.put(
            state.info.key, body, content_type=state.info.content_type, metadata=metadata, if_match=state.info.version
        )
    await lab.start_worker()
    result = await journey.sealed_consistently(run_id, "failed")
    assert result["failure"]["code"] == "attempt_execution_failed"
    assert result["sealed_state_digest_sha256"] is None
    snapshot = await journey.snapshot(run_id)
    assert snapshot["thread"]["head_run_id"] is None
    assert all(attempt["harness_run_id"] is None for attempt in snapshot["attempts"])
    assert (await live.evidence(case))["model_requests"] == 0
    await journey.restart_control()
    await lab.start_worker()
    await live.assert_stable(lambda: live.run(run_id), result, seconds=1)
    assert (await live.evidence(case))["model_requests"] == 0


@pytest.mark.parametrize("idempotent", [False, True], ids=["unowned-effect", "tool-owned-receipt"])
async def test_external_effect_before_checkpoint_requires_its_own_reconciliation(recovery, idempotent):
    journey, live, lab = recovery, recovery.live, recovery.lab
    agent = await journey.agent(plugins=journey.recovery_plugins())
    case = await live.case("recovery_effect")
    root = journey.case_root(case)
    (root / "plan.json").write_text(json.dumps({"idempotent": idempotent}))
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    await journey.marker(case, "effect_ready")
    state = await journey.state(receipt["run_id"])
    assert "effect-confirmed" not in state.envelope.harness.model_dump_json()
    await lab.stop(lab.workers[0], signal.SIGKILL)
    assert (root / "effect_attempts").read_text().splitlines() == ["attempt"]
    journey.release_marker(case, "effect_release")
    await lab.start_worker()
    result = await journey.sealed_consistently(receipt["run_id"])
    assert result["output_text"] == "effect-confirmed"
    assert (root / "effect_attempts").read_text().splitlines() == ["attempt", "attempt"]
    if idempotent:
        assert (root / "effect_receipt").read_text() == "effect-committed\n"
        assert not (root / "effects").exists()
    else:
        assert (root / "effects").read_text().splitlines() == ["effect-committed", "effect-committed"]
    await journey.restart_control()
    assert await live.run(receipt["run_id"]) == result
    assert (root / "effect_attempts").read_text().splitlines() == ["attempt", "attempt"]

"""Docker side effects survive a crash before relational lifecycle publication."""

import anyio
import pytest
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.storage import short_session

pytestmark = pytest.mark.anyio


async def environment_authority(journey, environment_id):
    async with short_session(journey.sessions) as session:
        row = await session.get(EnvironmentRecord, environment_id)
        return {
            "status": row.status,
            "state": row.state,
            "operation_id": row.operation_id,
            "operation_generation": row.operation_generation,
            "target_identity": row.target_identity,
        }


@pytest.mark.parametrize(
    "recovery", [{"environment": {"A13N_SERVICE_ENVIRONMENT_OPERATION_TIMEOUT_SECONDS": "15"}}], indirect=True
)
@pytest.mark.parametrize("window", ["target_started", "session_opened"])
async def test_docker_prepare_effect_is_reconciled_without_allocating_another_target(recovery, window):
    import docker

    journey, lab = recovery, recovery.lab
    environment, root = await journey.environment(provider_type="a13n.docker")
    environment_id = environment["id"]
    engine = docker.from_env()
    containers = []
    try:
        fault = (
            journey.arm("docker.start.after", where={"environment_id": environment_id})
            if window == "target_started"
            else journey.arm(
                "environment.publish.before",
                where={"environment_id": environment_id, "action": "prepare", "error": False},
            )
        )
        case = await journey.case(
            steps=[{"tool": "shell_exec", "arguments": {"command": "printf recovery > /workspace/proof.txt"}}]
        )
        receipt = await journey.start(case, environment={"environment_id": environment_id})
        hit = await journey.kill_at(fault)
        before = await environment_authority(journey, environment_id)
        assert before["operation_id"] is not None and before["state"] is None
        if window == "session_opened":
            assert hit["state"] is not None and before["operation_id"] == hit["operation_id"]
        containers = await anyio.to_thread.run_sync(
            lambda: engine.containers.list(all=True, filters={"label": f"io.a13n.environment-id={environment_id}"})
        )
        assert len(containers) == 1
        target_id = containers[0].id
        await journey.restart_control()
        # Provider operations have their own lease, independent of the shorter
        # Run Attempt lease. Reconciliation may claim only after it expires.
        await anyio.sleep(26)
        await lab.start_worker()
        result = await journey.sealed_consistently(
            receipt["run_id"], "completed" if window == "target_started" else "failed"
        )
        assert result["environment_id"] == environment_id
        if window == "target_started":
            assert (root / "proof.txt").read_text() == "recovery"
        else:
            # A killed HTTP client leaves an active EIP session. Reconciliation
            # recovers the target, but cannot silently steal session ownership.
            assert result["failure"]["code"] == "attempt_execution_failed"
            publications = journey.operations("environment.publish.after")
            assert any(row["action"] == "reconcile" and not row["error"] for row in publications)
            assert any(
                row["action"] == "prepare"
                and row["error_code"] == "provider_unavailable"
                and any("status 409" in cause["message"] for cause in row["error_causes"])
                for row in publications
            )
            assert not (root / "proof.txt").exists()
        remaining = await anyio.to_thread.run_sync(
            lambda: engine.containers.list(all=True, filters={"label": f"io.a13n.environment-id={environment_id}"})
        )
        assert [container.id for container in remaining] == [target_id]
        after = await environment_authority(journey, environment_id)
        assert after["state"] is not None and after["target_identity"]
        assert after["operation_id"] is None
        await journey.environment_command(environment_id, "delete")
        if window == "target_started":
            assert (root / "proof.txt").read_text() == "recovery", (
                "Environment deletion rolled back a user-owned bind file"
            )
    finally:
        # Only containers labelled with this newly allocated lab Environment are owned.
        containers = await anyio.to_thread.run_sync(
            lambda: engine.containers.list(all=True, filters={"label": f"io.a13n.environment-id={environment_id}"})
        )
        for container in containers:
            log = await anyio.to_thread.run_sync(lambda container=container: container.logs(tail=200))
            (lab.root / "docker-environment.log").write_bytes(log)
            await anyio.to_thread.run_sync(lambda container=container: container.remove(force=True))
        engine.close()

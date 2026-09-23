"""An environment journey: a run's shell tool acts in its sandbox, which is then stopped, reused and deleted.

`local` runs on every host. `docker` needs a reachable Engine and the native execution image
(`make image-docker-environment`); without them it is skipped, or fails under `--require-all`.
"""

import os
from contextlib import closing
from pathlib import Path

import docker
import pytest
from docker.errors import DockerException, ImageNotFound

from .api import eventually, expect
from .scripted import tool_results
from .stack import docker_host, unavailable

pytestmark = pytest.mark.anyio

DOCKER_IMAGE = os.environ.get("DOCKER_ENVIRONMENT_IMAGE", "a13n-docker-environment:local")


def engine() -> closing[docker.DockerClient]:
    return closing(docker.DockerClient(base_url=docker_host()))


def containers(environment_id: str) -> list:  # type: ignore[type-arg]
    with engine() as client:
        return client.containers.list(all=True, filters={"label": f"a13n.environment={environment_id}"})


def recipe(provider: str, directory: Path, config: pytest.Config) -> tuple[dict, dict]:
    """The provider configuration and template recipe of `provider` on this host."""
    if provider == "local":
        return {}, {
            "root": {"path": str(directory)},
            "shell_profiles": [{"profile_id": "default", "executable": "/bin/sh"}],
        }
    try:
        with engine() as client:
            client.images.get(DOCKER_IMAGE)
    except ImageNotFound:
        unavailable(config, f"{DOCKER_IMAGE} is not built; run make image-docker-environment")
    except DockerException as error:
        unavailable(config, f"No Docker Engine is reachable: {error}")
    # The account names no engine, so it uses the operator's.
    return {}, {"image": DOCKER_IMAGE}


def instance_exists(provider: str, directory: Path, environment_id: str) -> bool:
    if provider == "local":
        return (directory / environment_id).is_dir()
    return bool(containers(environment_id))


@pytest.mark.parametrize("provider", ["local", "docker"])
async def test_a_run_uses_its_environment_across_its_lifecycle(stack, provider, request) -> None:  # type: ignore[no-untyped-def]
    directory = stack.directory / "environments"
    directory.mkdir()
    config, environment_recipe = recipe(provider, directory, request.config)
    cleanup: list[str] = []
    try:
        await use_environment(stack, provider, directory, config, environment_recipe, cleanup)
    finally:
        # A failed journey must not leave containers behind; a passing one already deleted its instance.
        for environment_id in cleanup if provider == "docker" else ():
            for container in containers(environment_id):
                container.remove(force=True)


async def use_environment(stack, provider, directory, config, environment_recipe, cleanup) -> None:  # type: ignore[no-untyped-def]
    api, model = stack.api, stack.model
    provider_row = await api.client.post(
        f"{api.organization}/environment-providers",
        json={"workspace_id": None, "type": provider, "name": provider.title(), "config": config},
    )
    template = await api.client.post(
        f"{api.path}/environment-templates",
        json={
            "key": "box",
            "name": "Box",
            "provider_id": expect(provider_row, 201)["id"],
            "config": {"recipe": environment_recipe},
        },
    )
    agent = await api.create_agent(
        "builder",
        await api.create_model(model.base_url),
        default_environment_template_id=expect(template, 201)["id"],
    )

    command = "echo live-$((6 * 7)) > proof.txt && cat proof.txt"
    await model.call("shell_exec", {"command": command}, call_id="call_write", to="[write]")
    await model.say("Written.", to="[write]")
    receipt = await api.start(agent, "[write] Write the proof")
    thread_id = receipt["thread"]["id"]
    [mount] = receipt["run"]["environment_mounts"]
    environment_id, path = mount["environment_id"], f"{api.path}/environments/{mount['environment_id']}"
    cleanup.append(environment_id)
    run = await api.sealed(receipt["run"]["id"], timeout=120)
    assert (run["status"], run["output"]) == ("completed", "Written."), run["failure"]
    [_, answered] = await model.requests("[write]")
    assert "live-42" in tool_results(answered)[0]
    assert expect(await api.client.get(path), 200)["status"] == "ready"
    assert instance_exists(provider, directory, environment_id)

    # Stopping waits for no run; the next run starts the stopped instance, whose files survived.
    current = await api.client.get(path)
    stopping = await api.client.post(f"{path}/stop", headers={"if-match": current.headers["etag"]})
    assert expect(stopping, 202)["status"] == "stopping"

    async def stopped() -> bool:
        return expect(await api.client.get(path), 200)["status"] == "stopped"

    await eventually(stopped, timeout=60)
    await model.call("shell_exec", {"command": "cat proof.txt"}, call_id="call_read", to="[read]")
    await model.say("Read.", to="[read]")
    again = (await api.send(thread_id, agent, "[read] Read the proof"))["run"]
    assert again["environment_mounts"] == [mount]
    assert (await api.sealed(again["id"], timeout=120))["status"] == "completed"
    [_, answered] = await model.requests("[read]")
    assert "live-42" in tool_results(answered)[0]
    assert expect(await api.client.get(path), 200)["status"] == "ready"

    # Deletion requires the thread to stop mounting it first, then removes the instance.
    thread = await api.client.get(f"{api.path}/threads/{thread_id}")
    unmounted = await api.client.delete(
        f"{api.path}/threads/{thread_id}/environments/workspace", headers={"if-match": thread.headers["etag"]}
    )
    expect(unmounted, 204)
    current = await api.client.get(path)
    deleting = await api.client.delete(path, headers={"if-match": current.headers["etag"]})
    assert expect(deleting, 202)["status"] in {"deleting", "deleted"}

    async def deleted() -> bool:
        return expect(await api.client.get(path), 200)["status"] == "deleted"

    await eventually(deleted, timeout=60)
    assert not instance_exists(provider, directory, environment_id)

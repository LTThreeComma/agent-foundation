"""Restart object collection and fence delayed deletion against fresh publication."""

import signal
from uuid import uuid4

import anyio
import pytest
from a13n_service.assets.objects import asset_content_key
from a13n_service.object_retention.models import ObjectPublicationRecord
from a13n_service.object_retention.publication import PublicationObjectStore
from a13n_service.storage import ObjectConflict, short_session

from .client import agent_input
from .management_packages import upload

pytestmark = [pytest.mark.anyio, pytest.mark.parametrize("recovery", [{"collection_seconds": 1}], indirect=True)]


async def publication(journey, key):
    async with short_session(journey.sessions) as session:
        row = await session.get(ObjectPublicationRecord, key)
        return (
            None if row is None else {"generation": row.generation, "phase": row.phase, "version": row.object_version}
        )


@pytest.mark.parametrize("window", ["before_delete", "lost_delete_ack"])
async def test_orphan_collection_resumes_after_control_crash(recovery, window):
    journey, lab = recovery, recovery.lab
    key = f"organizations/{journey.live.config['organization_id']}/runs/run_{uuid4().hex}/state.json"
    fault = journey.arm(
        "objects.delete.before" if window == "before_delete" else "objects.delete.after",
        role="control",
        where={"key": key},
    )
    await journey.objects.put(key, b"unaccepted orphan", if_none_match=True)
    await journey.kill_at(fault)
    assert (await publication(journey, key))["phase"] == "collecting"
    assert await journey.exists(key) is (window == "before_delete")
    await lab.start_control()
    await lab.start_control(replica=True)
    await journey.live.wait(
        lambda: publication(journey, key), lambda value: value["phase"] == "collected", "abandoned collection settled"
    )
    assert not await journey.exists(key)
    await journey.restart_control()
    assert not await journey.exists(key)


async def test_stale_collector_cannot_delete_a_republished_object(recovery):
    journey, lab = recovery, recovery.lab
    key = f"organizations/{journey.live.config['organization_id']}/runs/run_{uuid4().hex}/state.json"
    fault = journey.arm("objects.delete.before", role="control", where={"key": key}, shield=True)
    original = await journey.objects.put(key, b"same immutable bytes", if_none_match=True)
    hit = await fault.reached()
    collector = next(process for process in lab.controls if process.pid == hit["pid"])
    lab.send(collector, signal.SIGSTOP)
    claim = await publication(journey, key)
    assert claim["phase"] == "collecting"
    await anyio.sleep(3.2)
    publisher = PublicationObjectStore(journey.objects, journey.sessions, timeout_seconds=5)
    with pytest.raises(ObjectConflict):
        await publisher.put(key, b"losing bytes must not replace content", if_none_match=True)
    renewed = await journey.objects.stat(key)
    assert renewed.version != original.version
    assert (await publication(journey, key))["generation"] != claim["generation"]
    # A new publication lease pins the object while the old collector resumes.
    async with journey.pending(publisher._begin(key)) as beginning:
        generation = await beginning
    fault.release()
    lab.send(collector, signal.SIGCONT)
    await anyio.sleep(0.5)
    await lab.stop(collector, signal.SIGKILL)
    assert (await journey.objects.stat(key)).version == renewed.version
    assert (await publication(journey, key))["generation"] == generation
    async with journey.objects.open(key) as reader:
        assert b"".join([chunk async for chunk in reader]) == b"same immutable bytes"
    await lab.start_control()
    await journey.live.wait(
        lambda: journey.exists(key), lambda value: not value, "unowned publication eventually expires"
    )


async def test_active_publication_and_retained_parent_namespaces_survive_collection(recovery):
    journey, live = recovery, recovery.live
    case = await live.case("remember")
    parent = await live.start(case)
    await journey.sealed_consistently(parent["run_id"])
    parent_state = await journey.state(parent["run_id"])
    thread = await live.thread(parent["thread_id"])
    successor = await journey.post(
        f"/api/v1/runs/{parent['run_id']}/continue",
        {"expected_thread_version": thread["version"], "input": agent_input("Recall the remembered token.")},
        expected=202,
    )
    live.track(successor)
    continued = await journey.sealed_consistently(successor["run_id"])
    assert continued["parent_run_id"] == parent["run_id"] and continued["output_text"] == case["token"]
    # An immutable payload can be referenced only inside retained checkpoint
    # history, so the owner pins the entire recognized Run payload namespace.
    payload_key = parent_state.info.key.removesuffix("state.json") + "payloads/agent-input/" + "a" * 64 + ".json"
    await journey.objects.put(payload_key, b"retained payload", if_none_match=True)
    key = f"organizations/{live.config['organization_id']}/runs/run_{uuid4().hex}/state.json"
    publisher = PublicationObjectStore(journey.objects, journey.sessions, timeout_seconds=5)
    await publisher._begin(key)
    await journey.objects.put(key, b"publication awaiting owner commit", if_none_match=True)
    await anyio.sleep(1.3)
    await journey.restart_control()
    assert await journey.exists(key), "Collector deleted an active publisher's object"
    assert (await journey.state(parent["run_id"])).body == parent_state.body
    assert await journey.exists(payload_key)
    await live.wait(lambda: journey.exists(key), lambda value: not value, "expired unaccepted publication collected")
    assert (await journey.state(parent["run_id"])).body == parent_state.body
    assert await journey.exists(payload_key)


async def test_asset_cleanup_retries_after_content_delete_ack_is_lost(recovery):
    journey, live, lab = recovery, recovery.live, recovery.lab
    asset = await upload(
        journey, "assets", b"cleanup proof", params={"filename": "proof.txt", "media_type": "text/plain"}
    )
    key = asset_content_key(
        organization_id=live.config["organization_id"], workspace_id=live.config["workspace_id"], asset_id=asset["id"]
    )
    fault = journey.arm("objects.delete.after", role="control", where={"key": key})
    assert (await live.http.delete(f"/api/v1/assets/{asset['id']}")).status_code == 204
    await journey.kill_at(fault)
    assert not await journey.exists(key)
    await lab.start_control()
    await lab.start_control(replica=True)

    # The retry is an idempotent delete of an absent object, not resurrection.
    async def deletions():
        return [value for value in journey.operations("objects.delete.after") if value["key"] == key]

    await live.wait(
        deletions,
        lambda values: len(values) == 2,
        "asset cleanup retry acknowledged",
    )
    assert (await live.http.get(f"/api/v1/assets/{asset['id']}/content")).status_code == 404
    assert not await journey.exists(key)

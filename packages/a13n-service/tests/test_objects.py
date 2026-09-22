"""Real local object publication, cross-process fencing and uncertain writes."""

import asyncio
import fcntl
import hashlib
import json
import multiprocessing
from pathlib import Path

import pytest
from a13n_service.infra.objects.local import LocalObjects, ObjectConflict, ObjectCorrupt

pytestmark = pytest.mark.anyio


def _contend(root, expected, writer, ready, release, results):
    async def attempt():
        ready.put(writer)
        release.wait(10)
        try:
            result = await LocalObjects(Path(root)).replace_snapshot(
                "state", b"candidate", expected=expected, writer=writer
            )
            results.put(("won", result.version, result.writer))
        except ObjectConflict:
            results.put(("conflict", None, writer))

    asyncio.run(attempt())


async def test_payload_is_create_only_and_retries_identical_bytes(tmp_path):
    store = LocalObjects(tmp_path)
    initial = await store.create_payload("orgs/org_a/entries/inb_a/payload/digest", b"input")
    assert await store.create_payload(initial.key, b"input") == initial
    assert initial.digest == hashlib.sha256(b"input").hexdigest()
    with pytest.raises(ObjectConflict):
        await store.create_payload(initial.key, b"other")
    with pytest.raises(ObjectConflict):
        await store.replace_snapshot(initial.key, b"input", expected=initial.version, writer=1)
    assert await store.read(initial.key) == initial


async def test_same_content_takeover_rotates_token_and_rejects_old_writer(tmp_path):
    store = LocalObjects(tmp_path)
    original = await store.replace_snapshot("state", b"checkpoint", expected=None, writer=1)
    takeover = await store.replace_snapshot("state", original.content, expected=original.version, writer=2)
    assert takeover.version != original.version
    assert takeover.content == original.content
    with pytest.raises(ObjectConflict):
        await store.replace_snapshot("state", b"stale", expected=original.version, writer=1)
    with pytest.raises(ObjectConflict):
        await store.replace_snapshot("state", b"stale", expected=takeover.version, writer=1)
    assert await store.read("state") == takeover


async def test_two_processes_cannot_both_replace_the_same_version(tmp_path):
    original = await LocalObjects(tmp_path).replace_snapshot("state", b"original", expected=None, writer=1)
    context = multiprocessing.get_context("spawn")
    ready, results, release = context.Queue(), context.Queue(), context.Event()
    processes = [
        context.Process(target=_contend, args=(str(tmp_path), original.version, writer, ready, release, results))
        for writer in (2, 3)
    ]
    try:
        for process in processes:
            process.start()
        await asyncio.to_thread(ready.get, True, 15)
        await asyncio.to_thread(ready.get, True, 15)
        release.set()
        outcomes = [await asyncio.to_thread(results.get, True, 15) for _ in processes]
        assert sorted(result[0] for result in outcomes) == ["conflict", "won"]
        winner = next(result for result in outcomes if result[0] == "won")
        current = await LocalObjects(tmp_path).read("state")
        assert current is not None
        assert (current.version, current.writer, current.content) == (winner[1], winner[2], b"candidate")
    finally:
        release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        ready.close()
        results.close()


async def test_lost_ack_can_be_reconciled_by_exact_read_and_claim(tmp_path, monkeypatch):
    import a13n_service.infra.objects.local as local

    store = LocalObjects(tmp_path)
    original = await store.replace_snapshot("state", b"before", expected=None, writer=1)
    real_replace = local.os.replace

    def lost_ack(source, target):
        real_replace(source, target)
        raise OSError("publication outcome unknown")

    with monkeypatch.context() as patch:
        patch.setattr(local.os, "replace", lost_ack)
        with pytest.raises(OSError, match="unknown"):
            await store.replace_snapshot("state", b"after", expected=original.version, writer=1)
    uncertain = await store.read("state")
    assert uncertain is not None
    assert uncertain.content == b"after" and uncertain.writer == 1 and uncertain.version != original.version
    # A read establishes exact content; a fresh durable CAS establishes the replacement owner's claim.
    claimed = await store.replace_snapshot("state", uncertain.content, expected=uncertain.version, writer=2)
    assert claimed.content == b"after" and claimed.version != uncertain.version
    assert not list(tmp_path.rglob("*.tmp"))


async def test_failure_before_replace_preserves_durable_current(tmp_path, monkeypatch):
    import a13n_service.infra.objects.local as local

    store = LocalObjects(tmp_path)
    original = await store.replace_snapshot("state", b"before", expected=None, writer=1)

    def fail_replace(source, target):
        raise OSError("not published")

    monkeypatch.setattr(local.os, "replace", fail_replace)
    with pytest.raises(OSError, match="not published"):
        await store.replace_snapshot("state", b"after", expected=original.version, writer=1)
    assert await store.read("state") == original
    assert not list(tmp_path.rglob("*.tmp"))


async def test_corrupt_envelope_and_finite_lock_wait_fail_closed(tmp_path):
    store = LocalObjects(tmp_path, lock_timeout=0.02)
    original = await store.replace_snapshot("state", b"before", expected=None, writer=1)
    path = store._path("state")
    with path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="lock"):
            await asyncio.wait_for(store.replace_snapshot("state", b"after", expected=original.version, writer=1), 2)
    envelope = json.loads(path.read_bytes())
    envelope["digest"] = "wrong"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ObjectCorrupt):
        await store.read("state")
    with pytest.raises(ObjectCorrupt):
        await store.replace_snapshot("state", b"after", expected=original.version, writer=1)


async def test_object_bounds_and_logical_paths(tmp_path):
    store = LocalObjects(tmp_path, max_bytes=3)
    with pytest.raises(ValueError, match="bounds"):
        await store.create_payload("too_large", b"1234")
    payload = await store.create_payload("../../logical/key", b"123")
    assert await store.read(payload.key) == payload
    assert all(path.is_relative_to(tmp_path) for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="key"):
        await store.read("invalid\x00key")


@pytest.mark.parametrize("published", [True, False])
async def test_snapshot_writer_reconciles_only_its_exact_uncertain_publication(tmp_path, monkeypatch, published):
    import a13n_service.infra.objects.local as local
    from a13n_service.runs.snapshots import replace

    store = LocalObjects(tmp_path)
    original = await store.replace_snapshot("state", b"before", expected=None, writer=1)
    real_replace = local.os.replace

    def uncertain(source, target):
        if published:
            real_replace(source, target)
        raise OSError("unknown publication")

    with monkeypatch.context() as patch:
        patch.setattr(local.os, "replace", uncertain)
        if published:
            reconciled = await replace(store, "state", b"after", expected=original.version, writer=1, timeout=1)
            assert reconciled == await store.read("state")
            assert reconciled.version != original.version
        else:
            with pytest.raises(OSError, match="unknown"):
                await replace(store, "state", b"after", expected=original.version, writer=1, timeout=1)
            assert await store.read("state") == original


async def test_uncertain_publication_cannot_adopt_competitor_before_readback(tmp_path, monkeypatch):
    from a13n_service.runs.snapshots import replace

    store = LocalObjects(tmp_path)
    old = await store.replace_snapshot("state", b"initial", expected=None, writer=1)
    actual = store.replace_snapshot
    competitor = None

    async def lose_ack_then_takeover(key, content, **kwargs):
        nonlocal competitor
        published = await actual(key, content, **kwargs)
        # Exact same semantic bytes, different physical writer/version before the failed caller can read back.
        competitor = await actual(key, content, expected=published.version, writer=2)
        raise OSError("acknowledgement lost before competing takeover")

    monkeypatch.setattr(store, "replace_snapshot", lose_ack_then_takeover)
    with pytest.raises(OSError, match="lost"):
        await replace(store, "state", b"candidate", expected=old.version, writer=1, timeout=1)
    assert await store.read("state") == competitor
    assert competitor.writer == 2
    with pytest.raises(ObjectConflict):
        await actual("state", b"candidate", expected=competitor.version, writer=1)


async def test_older_dispatched_thread_write_finishes_after_same_content_takeover(tmp_path, monkeypatch):
    from threading import Event

    old_store, newer_store = LocalObjects(tmp_path), LocalObjects(tmp_path)
    initial = await old_store.replace_snapshot("state", b"same", expected=None, writer=1)
    dispatched, release = Event(), Event()
    original = old_store._write

    def delayed(*args, **kwargs):
        dispatched.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(old_store, "_write", delayed)
    older = asyncio.create_task(old_store.replace_snapshot("state", b"same", expected=initial.version, writer=1))
    try:
        assert await asyncio.to_thread(dispatched.wait, 3)
        newer = await newer_store.replace_snapshot("state", b"same", expected=initial.version, writer=2)
        assert newer.version != initial.version
        release.set()
        with pytest.raises(ObjectConflict):
            await older
        assert await newer_store.read("state") == newer
    finally:
        release.set()
        await asyncio.gather(older, return_exceptions=True)

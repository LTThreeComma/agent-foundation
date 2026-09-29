"""Checkpoint codecs leave the event loop without changing bytes or publication boundaries."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness import HarnessState
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.runs import checkpoints
from a13n_service.runs.attempts import AttemptControl, Lease, LeaseLost
from a13n_service.runs.checkpoints import RunState
from a13n_service.runs.display import Display, StreamPosition
from pydantic_ai.messages import BinaryContent, ModelRequest, UserPromptPart

pytestmark = pytest.mark.anyio


@pytest.fixture
def codec_runtime(tmp_path):
    objects = LocalObjects(tmp_path, max_bytes=1024 * 1024, timeout=5)
    return SimpleNamespace(objects=objects, settings=SimpleNamespace(objects=SimpleNamespace(timeout=5)))


def boundary():
    lease = Lease("run_codec", "rat_codec", "thr_codec", "org_codec", "ws_codec", 1, "worker", "token")
    state = RunState(
        harness=HarnessState.new(
            thread_id=lease.thread_id,
            message_history=[
                ModelRequest(parts=[UserPromptPart([BinaryContent(b"binary\x00", media_type="image/png")])])
            ],
        ),
        seq=1,
        attempt=1,
    )
    return lease, state, Display(position=StreamPosition(attempt=1, sequence=3), resume_after="123-0")


async def test_checkpoint_codecs_run_off_loop_and_preserve_exact_bytes(codec_runtime, monkeypatch):
    lease, state, display = boundary()
    expected_state, expected_display = state.model_dump_json().encode(), display.model_dump_json().encode()
    loop_thread = threading.get_ident()
    calls = []
    for model in (RunState, Display):
        encode, decode = model.model_dump_json, model.model_validate_json

        def dump(value, *, encode=encode, model=model, **kwargs):
            assert threading.get_ident() != loop_thread
            calls.append((model, "encode"))
            return encode(value, **kwargs)

        def validate(cls, value, *, decode=decode, model=model, **kwargs):
            assert threading.get_ident() != loop_thread
            calls.append((model, "decode"))
            return decode(value, **kwargs)

        monkeypatch.setattr(model, "model_dump_json", dump)
        monkeypatch.setattr(model, "model_validate_json", classmethod(validate))

    committed = await checkpoints.publish_checkpoint(codec_runtime, lease, state, display, control=AttemptControl())
    assert (
        await codec_runtime.objects.get(
            f"{checkpoints.prefix(lease.organization_id, lease.run_id, 'state')}/{committed.state.digest}"
        )
        == expected_state
    )
    assert (
        await codec_runtime.objects.get(
            f"{checkpoints.prefix(lease.organization_id, lease.run_id, 'display')}/{committed.display.digest}"
        )
        == expected_display
    )
    assert (
        await checkpoints.load_state(codec_runtime.objects, lease.organization_id, lease.run_id, committed.state)
        == state
    )
    restored = await checkpoints.load_display(
        codec_runtime.objects, lease.organization_id, lease.run_id, committed.display
    )
    assert restored == display
    terminal = await checkpoints.publish_display(codec_runtime, lease, display, control=AttemptControl())
    assert terminal == committed.display
    assert calls == [
        (RunState, "encode"),
        (Display, "encode"),
        (RunState, "decode"),
        (Display, "decode"),
        (Display, "encode"),
    ]


@pytest.mark.parametrize("terminal", [False, True])
async def test_lease_is_rechecked_after_codec_wait_before_any_object_write(codec_runtime, monkeypatch, terminal):
    lease, state, display = boundary()
    control = AttemptControl()
    encode = Display.model_dump_json

    def expire(value, **kwargs):
        control.deadline = 0  # The lease expired while this codec waited or ran.
        return encode(value, **kwargs)

    monkeypatch.setattr(Display, "model_dump_json", expire)
    put = AsyncMock(wraps=codec_runtime.objects.put)
    monkeypatch.setattr(codec_runtime.objects, "put", put)
    with pytest.raises(LeaseLost):
        if terminal:
            await checkpoints.publish_display(codec_runtime, lease, display, control=control)
        else:
            await checkpoints.publish_checkpoint(codec_runtime, lease, state, display, control=control)
    put.assert_not_awaited()


async def test_paired_publication_still_joins_the_other_write_on_failure(codec_runtime, monkeypatch):
    lease, state, display = boundary()
    started, release = asyncio.Event(), asyncio.Event()
    put = codec_runtime.objects.put

    async def failing(key, data, *, content_type):
        if "/state/" in key:
            raise ValueError("state write failed")
        started.set()
        await release.wait()
        return await put(key, data, content_type=content_type)

    monkeypatch.setattr(codec_runtime.objects, "put", failing)
    task = asyncio.create_task(
        checkpoints.publish_checkpoint(codec_runtime, lease, state, display, control=AttemptControl())
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert not task.done()
        release.set()
        with pytest.raises(ValueError, match="state write failed"):
            await task
        assert (
            len(
                await codec_runtime.objects.keys(
                    checkpoints.prefix(lease.organization_id, lease.run_id, "display"), limit=10
                )
            )
            == 1
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

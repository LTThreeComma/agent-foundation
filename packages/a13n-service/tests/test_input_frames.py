"""Complete Service frames survive native serialization and reject partial ownership."""

from copy import deepcopy

import pytest
from a13n_harness import HarnessState
from a13n_harness.model_context import ModelInputEvent
from a13n_service.infra.errors import ServiceError
from a13n_service.runs import input_frames
from a13n_service.runs.input_frames import PreparedInputs
from a13n_stream_protocol import InputSource
from pydantic_ai.messages import BinaryContent, EnqueuedMessagesEvent, ModelRequest, TextContent, UserPromptPart


def _history(*parts: list) -> HarnessState:
    return HarnessState.new(message_history=(ModelRequest(parts=[UserPromptPart(part) for part in parts]),))


def test_complete_frames_select_only_exact_native_positions():
    prepared = PreparedInputs("run_test")
    first = prepared.offer("inb_first", [TextContent("A"), BinaryContent(b"img1", media_type="image/png")])
    second = prepared.offer("inb_second", [BinaryContent(b"img2", media_type="image/png")])
    mixed = [TextContent("before"), *first, TextContent("between"), *second, TextContent("after")]
    source = ModelInputEvent(content=mixed)
    expected = {1, 2, 3, 5, 6}
    assert prepared.owned_coordinates(source) == {
        InputSource(kind="model_input", content_index=index) for index in expected
    }
    event = EnqueuedMessagesEvent(
        enqueue_id="test",
        messages=(
            ModelRequest(parts=[UserPromptPart([TextContent("unowned"), *first])]),
            ModelRequest(parts=[UserPromptPart([TextContent("between"), *second, TextContent("after")])]),
        ),
    )
    owned = prepared.owned_coordinates(event)
    assert InputSource(kind="enqueued_messages", message_index=0, part_index=0, content_index=0) not in owned
    assert InputSource(kind="enqueued_messages", message_index=1, part_index=0, content_index=0) not in owned
    assert len(owned) == len(first) + len(second)
    repeated = EnqueuedMessagesEvent(
        enqueue_id="duplicate",
        messages=(ModelRequest(parts=[UserPromptPart(first)]), ModelRequest(parts=[UserPromptPart(first)])),
    )
    with pytest.raises(ServiceError):
        prepared.owned_coordinates(repeated)
    assert prepared.incorporated(_history(mixed).message_history) == ("inb_first", "inb_second")


@pytest.mark.parametrize(
    "damage",
    [
        "different_bytes",
        "truncated",
        "inserted",
        "reordered",
        "count",
        "digest",
        "duplicate",
        "cross_part",
    ],
)
def test_damaged_recognized_frame_never_receipts_or_hides_partial_content(damage):
    prepared = PreparedInputs("run_test")
    frame = prepared.offer("inb_first", [TextContent("A"), BinaryContent(b"img1", media_type="image/png")])
    altered = deepcopy(frame)
    if damage == "different_bytes":
        altered[2] = BinaryContent(b"img2", media_type="image/png")
    elif damage == "truncated":
        altered.pop()
    elif damage == "inserted":
        altered.insert(2, TextContent("intruder"))
    elif damage == "reordered":
        altered[1], altered[2] = altered[2], altered[1]
    elif damage == "count":
        altered[0].metadata["a13n.service.input"]["count"] = 1
    elif damage == "digest":
        altered[0].metadata["a13n.service.input"]["digest"] = "0" * 64
    elif damage == "duplicate":
        altered.extend(deepcopy(frame))
    elif damage == "cross_part":
        with pytest.raises(ServiceError):
            prepared.incorporated(_history(altered[:2], altered[2:]).message_history)
        return
    with pytest.raises(ServiceError):
        prepared.incorporated(_history(altered).message_history)
    with pytest.raises(ServiceError):
        prepared.owned_coordinates(ModelInputEvent(content=altered))


def test_wrong_run_and_unissued_metadata_cannot_claim_input():
    prepared = PreparedInputs("run_test")
    valid = prepared.offer("inb_first", [TextContent("valid")])
    wrong_run = deepcopy(valid)
    wrong_run[0].metadata["a13n.service.input"]["run_id"] = "run_other"
    assert prepared.owned_coordinates(ModelInputEvent(content=wrong_run)) == set()
    assert prepared.incorporated(_history(wrong_run).message_history) == ()
    unissued = deepcopy(valid)
    unissued[0].metadata["a13n.service.input"]["entry_id"] = "inb_unissued"
    with pytest.raises(ServiceError):
        prepared.incorporated(_history(unissued).message_history)


def test_receipt_uses_detached_export_and_recovery_uses_saved_frame():
    prepared = PreparedInputs("run_test")
    live = prepared.offer("inb_first", [BinaryContent(b"img1", media_type="image/png")])
    saved = _history(live)
    live[1].data = b"img2"
    assert prepared.incorporated(saved.message_history) == ("inb_first",)
    with pytest.raises(ServiceError):
        prepared.incorporated(_history(live).message_history)

    recovered = PreparedInputs("run_test", receipts=("inb_first",))
    recovered.restore(saved.message_history)
    assert recovered.owned_coordinates(ModelInputEvent(content=list(saved.message_history[0].parts[0].content)))
    assert recovered.incorporated(saved.message_history) == ("inb_first",)


def test_confirmed_frame_skips_checkpoint_rehash_but_visibility_and_recovery_validate(monkeypatch):
    prepared = PreparedInputs("run_test")
    frame = prepared.offer("inb_first", [BinaryContent(b"original", media_type="image/png")])
    saved = _history(frame)
    hashes = 0
    actual_digest = input_frames.payload_digest

    def count_digest(items):
        nonlocal hashes
        hashes += 1
        return actual_digest(items)

    monkeypatch.setattr(input_frames, "payload_digest", count_digest)
    assert prepared.incorporated(saved.message_history) == ("inb_first",)
    prepared.confirm(("inb_first",))
    for _ in range(3):
        assert prepared.incorporated(saved.message_history) == ("inb_first",)
    assert hashes == 1

    changed = deepcopy(frame)
    changed[1] = BinaryContent(b"changed", media_type="image/png")
    with pytest.raises(ServiceError):
        prepared.owned_coordinates(ModelInputEvent(content=changed))
    recovered = PreparedInputs("run_test", receipts=("inb_first",))
    with pytest.raises(ServiceError):
        recovered.restore(_history(changed).message_history)
    assert hashes == 3

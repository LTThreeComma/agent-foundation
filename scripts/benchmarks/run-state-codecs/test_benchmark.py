"""Correctness checks for the benchmark, not performance assertions."""

from __future__ import annotations

import io
import json

import pytest
from a13n_harness.codeact.config import CodeActConfig
from a13n_harness.codeact.values import bounded_json_size
from a13n_harness.toolsets.codeact_state import CODEACT_STATE_ID, CodeActStoredValues
from a13n_service.storage.codec import canonical_model_bytes, decode_canonical_model
from benchmark import ADAPTER, PROFILES, codec_memory, local_trial, make_state, summary
from codecs_bench import CODECS, decode, encode
from PIL import Image
from pydantic_ai.messages import BinaryContent, UserPromptPart


@pytest.mark.parametrize("profile", PROFILES)
def test_fixtures_are_deterministic_canonical_envelopes(profile):
    first = make_state(profile, 16 * 1024)
    body = canonical_model_bytes(first)
    assert body == canonical_model_bytes(make_state(profile, 16 * 1024))
    assert canonical_model_bytes(decode_canonical_model(body, ADAPTER)) == body
    assert first.checkpoint_kind == "progress"
    assert first.checkpoint_seq == 1


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("codec", CODECS)
def test_codec_round_trip(profile, codec):
    body = canonical_model_bytes(make_state(profile, 16 * 1024))
    assert decode(encode(body, codec), codec, len(body)) == body


def test_images_survive_real_message_serialization():
    state = make_state("image-jpeg", 16 * 1024)
    restored = decode_canonical_model(canonical_model_bytes(state), ADAPTER)
    images = []
    for message in restored.harness.message_history:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                images.extend(value for value in part.content if isinstance(value, BinaryContent))
    assert len(images) >= 1
    for image in images:
        assert image.media_type == "image/jpeg"
        with Image.open(io.BytesIO(image.data)) as decoded:
            assert decoded.format == "JPEG"
            decoded.verify()
    assert len({image.data for image in images}) == len(images)


def test_codeact_uses_actual_stored_values_schema():
    state = make_state("codeact-values", 16 * 1024)
    entry = state.harness.agent_context_state.entries[CODEACT_STATE_ID]
    assert entry.version == "1"
    values = CodeActStoredValues.model_validate(entry.data)
    assert values.values["cursor"] == len(values.values["records"])
    assert bounded_json_size(values.model_dump(), CodeActConfig().max_state_bytes) > 0
    assert values.values["records"][0]["id"] not in json.dumps(state.harness.message_history, default=str)


@pytest.mark.parametrize("codec", ["zstd-1", "zstd-3", "lz4-0"])
def test_bad_frames_fail(codec):
    import zstandard

    body = b"benchmark bytes" * 1000
    encoded = encode(body, codec)
    corrupt = encoded[:-1] + bytes([encoded[-1] ^ 0xFF])
    for bad in (encoded[:-1], encoded + b"trailing", corrupt):
        with pytest.raises((ValueError, RuntimeError, zstandard.ZstdError)):
            decode(bad, codec, len(body))
    with pytest.raises(ValueError, match="size"):
        decode(encoded, codec, len(body) - 1)


@pytest.mark.parametrize("codec", CODECS)
def test_wrong_decoded_length_fails(codec):
    with pytest.raises(ValueError, match="size"):
        decode(encode(b"123", codec), codec, 2)


def test_summary_keeps_raw_samples_and_nearest_rank_p95():
    assert summary([5.0, 1.0, 3.0]) == {"median": 3.0, "p95": 5.0, "samples": [5.0, 1.0, 3.0]}


@pytest.mark.parametrize("codec", CODECS)
def test_local_boundary_round_trip(tmp_path, codec):
    import asyncio

    result = asyncio.run(local_trial(make_state("entropy", 4096), codec, 2, 2, tmp_path / codec))
    assert result["operation_pairs"] == 4
    assert len(result["checkpoint_ms"]["samples"]) == 4
    assert len(result["recovery_ms"]["samples"]) == 4
    assert result["local_framed_file_bytes"] == result["body_bytes_per_put_or_get"] + 65548


def test_memory_subprocess_reports_each_operation(tmp_path):
    result = codec_memory(b"sample" * 1024, "zstd-3", tmp_path)
    for operation in ("encode", "decode"):
        assert len(result[operation]) == 3
        for sample in result[operation]:
            assert sample["peak_rss_kib"] >= sample["before_hwm_kib"] > 0
            assert sample["output_bytes"] > 0
    assert result["decode"][0]["output_bytes"] == 6 * 1024

"""Reproducible synthetic RunStateEnvelope codec and local storage benchmark.

Run with the repository's locked Python 3.13 environment plus requirements.txt.
No model calls, production data, database, or production storage modifications.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import lz4
import zstandard
from a13n_harness import HarnessState
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.toolsets.codeact_state import CODEACT_STATE_ID, CodeActStoredValues
from a13n_service.agents.domain import EffectiveAgentConfig, canonical_digest
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.storage.codec import canonical_model_bytes, decode_canonical_model
from a13n_service.storage.object_store import LocalObjectStore
from codecs_bench import CODECS, decode, encode
from PIL import Image
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

ROOT = Path(__file__).resolve().parents[3]
ADAPTER = TypeAdapter(RunStateEnvelope)
NOW = datetime(2026, 9, 7, tzinfo=UTC)
SIZES = {"small": 16 * 1024, "medium": 256 * 1024, "large": 4 * 1024 * 1024, "xlarge": 32 * 1024 * 1024}
PROFILES = ("repository-history", "tool-results", "entropy", "image-jpeg", "codeact-values")


def make_state(profile: str, target: int, seed: int = 213) -> RunStateEnvelope:
    """Build schema-valid synthetic progress states with fixed IDs and timestamps.

    target is a content budget, not the final serialized envelope byte count.
    History samples distinct chunks from tracked source/docs; repeated paths and
    prose are deliberately visible, not a claim of production representativeness.
    """
    rng = random.Random(seed)
    messages = []
    context = AgentContextStateSnapshot()
    if profile == "image-jpeg":
        total = 0
        dimension = min(1024, max(128, int(math.sqrt(target))))
        while total < target:
            # Unique noisy RGB images, already JPEG-compressed; a hard image case,
            # not representative photography and never a repeated identical blob.
            image = Image.frombytes("RGB", (dimension, dimension), rng.randbytes(dimension * dimension * 3))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85, optimize=False)
            jpeg = buffer.getvalue()
            total += len(jpeg)
            messages.append(
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            [
                                "Inspect this synthetic test image.",
                                BinaryContent(data=jpeg, media_type="image/jpeg"),
                            ],
                            timestamp=NOW,
                        )
                    ]
                )
            )
            messages.append(
                ModelResponse(
                    parts=[TextPart("Synthetic noisy image received.")], timestamp=NOW, model_name="synthetic"
                )
            )
    elif profile == "codeact-values":
        # Explicit CodeAct JSON values, NOT Monty interpreter heap/pickle snapshots.
        rows = []
        total = 0
        while total < target:
            row = {
                "id": f"record_{rng.getrandbits(64):016x}",
                "score": rng.random(),
                "embedding": [round(rng.uniform(-1, 1), 6) for _ in range(32)],
                "tags": rng.sample(["python", "storage", "agent", "async", "tests", "tools"], 3),
            }
            rows.append(row)
            total += len(json.dumps(row).encode())
        stored = CodeActStoredValues(values={"records": rows, "cursor": len(rows)})
        context = AgentContextStateSnapshot(
            entries={CODEACT_STATE_ID: CapabilityState(version="1", data=stored.model_dump(mode="json"))}
        )
        messages = [ModelRequest(parts=[UserPromptPart("Resume analysis from the stored records.", timestamp=NOW)])]
    elif profile == "repository-history":
        sources = (
            sorted((ROOT / "docs").rglob("*.md"))
            + sorted((ROOT / "spec").rglob("*.md"))
            + sorted((ROOT / "packages/a13n-service/a13n_service").rglob("*.py"))
        )
        rng.shuffle(sources)
        content = "\n".join(f"File: {p.relative_to(ROOT)}\n{p.read_text()}" for p in sources)
        # At tested sizes this corpus is larger than the budget; no repeated corpus.
        if len(content.encode()) < target:
            raise ValueError("repository corpus is smaller than requested history")
        text = content.encode()[:target].decode("utf-8", errors="ignore")
        for i, offset in enumerate(range(0, len(text), 8192)):
            messages.extend(
                [
                    ModelRequest(
                        parts=[UserPromptPart(f"Review source excerpt {i}; explain behavior and risks.", timestamp=NOW)]
                    ),
                    ModelResponse(
                        parts=[TextPart(text[offset : offset + 8192])], timestamp=NOW, model_name="synthetic"
                    ),
                ]
            )
    elif profile in ("tool-results", "entropy"):
        consumed = 0
        i = 0
        while consumed < target:
            if profile == "entropy":
                result = {"blob_base64": base64.b64encode(rng.randbytes(min(6144, target - consumed))).decode()}
            else:
                result = {
                    "rows": [
                        {
                            "id": f"record_{rng.getrandbits(64):016x}",
                            "path": f"src/module_{rng.randrange(200)}/file_{rng.randrange(1000)}.py",
                            "line": rng.randrange(1, 2000),
                            "status": rng.choice(["passed", "failed", "skipped"]),
                            "duration_ms": rng.randrange(1, 10000),
                            "summary": rng.choice(
                                [
                                    "Validated conditional publication and writer ownership.",
                                    "Read complete state and checked the expected version.",
                                    "工具执行完成; 保留完整的运行状态。",
                                    "Retry after a transient storage error without losing provenance.",
                                ]
                            ),
                        }
                        for _ in range(32)
                    ]
                }
            consumed += len(json.dumps(result, ensure_ascii=False).encode())
            call_id = f"call_{i:08d}"
            messages.extend(
                [
                    ModelResponse(
                        parts=[ToolCallPart("inspect_records", {"page": i}, tool_call_id=call_id)],
                        timestamp=NOW,
                        model_name="synthetic",
                    ),
                    ModelRequest(
                        parts=[ToolReturnPart("inspect_records", result, tool_call_id=call_id, timestamp=NOW)]
                    ),
                ]
            )
            i += 1
    else:
        raise ValueError(profile)
    config_payload = {
        "resolved_model": {
            "execution": {
                "model_id": "mdl_1234567890abcdef",
                "model_key": "benchmark",
                "upstream_model": "synthetic",
                "model_api": "openai.responses",
            },
            "settings": {},
            "characteristics": {"context_window": 128000},
        },
        "runtime_lock_digest": "a" * 64,
        "instructions": "Inspect the supplied synthetic records. No external calls are made.",
        "input_adapter": {"adapter_key": "native", "config": {}},
        "protocol": {"schema_version": "1", "public_name": "Benchmark", "output_modes": ["text"], "limits": {}},
        "content_digest": "0" * 64,
    }
    config = EffectiveAgentConfig.model_validate(config_payload)
    config = config.model_copy(
        update={"content_digest": canonical_digest(config.model_dump(mode="json", exclude={"content_digest"}))}
    )
    harness = HarnessState.new(thread_id="thr_1234567890abcdef", message_history=messages, agent_context_state=context)
    return RunStateEnvelope(
        run_id="run_1234567890abcdef",
        thread_id=harness.thread_id,
        checkpoint_seq=1,
        checkpoint_kind="progress",
        input_disposition="applied",
        last_checkpoint_run_attempt_id="rat_1234567890abcdef",
        last_checkpoint_fence=1,
        agent_id="agt_1234567890abcdef",
        agent_revision_id="agr_1234567890abcdef",
        effective_agent_config=config,
        runtime_lock_digest="a" * 64,
        harness_schema_version="1",
        harness=harness,
    )


def summary(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {"median": statistics.median(values), "p95": ordered[math.ceil(len(values) * 0.95) - 1], "samples": values}


def timed(function: Callable[[], Any], repeats: int) -> dict[str, Any]:
    function()  # warmup, including imports/internal codec setup
    wall, cpu = [], []
    for _ in range(repeats):
        t0, c0 = time.perf_counter_ns(), time.process_time_ns()
        result = function()
        cpu.append((time.process_time_ns() - c0) / 1e6)
        wall.append((time.perf_counter_ns() - t0) / 1e6)
        del result  # destruction is outside the timed operation
    return {"wall_ms": summary(wall), "cpu_ms": summary(cpu)}


def codec_memory(raw: bytes, codec: str, directory: Path) -> dict[str, Any]:
    raw_path, encoded_path = directory / "raw", directory / "encoded"
    raw_path.write_bytes(raw)
    encoded_path.write_bytes(encode(raw, codec))
    results = {}
    for operation, path in (("encode", raw_path), ("decode", encoded_path)):
        results[operation] = [
            json.loads(
                subprocess.check_output(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("memory.py")),
                        str(path),
                        codec,
                        operation,
                        str(len(raw)),
                    ],
                    text=True,
                )
            )
            for _ in range(3)
        ]
    return results


async def local_trial(state: RunStateEnvelope, codec: str, concurrency: int, rounds: int, root: Path) -> dict[str, Any]:
    """Boundary harness, not RunStateStore or full Service checkpoint/recovery.

    Matches current synchronous canonical serialization/strict validation, moving
    only the additional compression/decompression to worker threads. Includes
    SHA-256 of encoded bytes, conditional PUT, actual local framing, and GET.
    No database, RunStateStore fencing/sealing, model, or external network.
    """
    store = await LocalObjectStore.create(root)
    expected = canonical_model_bytes(state)
    encoded = encode(expected, codec)
    initial = [await store.put(f"run-{i}", encoded, if_none_match=True) for i in range(concurrency)]
    checkpoint_ms, recovery_ms, lag_ms = [], [], []
    interval = 0.005
    stop = asyncio.Event()

    async def ticker() -> None:
        while not stop.is_set():
            deadline = time.perf_counter() + interval
            await asyncio.sleep(interval)
            lag_ms.append(max(0, time.perf_counter() - deadline) * 1000)

    async def worker(index: int, measured: bool) -> None:
        info = initial[index]
        start = time.perf_counter()
        raw = canonical_model_bytes(state)
        body = raw if codec == "raw" else await anyio.to_thread.run_sync(encode, raw, codec)
        digest = hashlib.sha256(body).hexdigest()
        info = await store.put(f"run-{index}", body, if_match=info.version, metadata={"sha256": digest})
        checkpoint_end = time.perf_counter()
        async with store.open(info.key) as reader:
            restored = b"".join([chunk async for chunk in reader])
            assert hashlib.sha256(restored).hexdigest() == reader.info.metadata["sha256"]
        decoded = restored if codec == "raw" else await anyio.to_thread.run_sync(decode, restored, codec, len(expected))
        envelope = decode_canonical_model(decoded, ADAPTER)
        end = time.perf_counter()
        # Correctness checks outside timed recovery; serialization itself was timed above.
        assert decoded == expected and envelope.run_id == state.run_id
        initial[index] = info
        if measured:
            checkpoint_ms.append((checkpoint_end - start) * 1000)
            recovery_ms.append((end - checkpoint_end) * 1000)

    await asyncio.gather(*(worker(i, False) for i in range(concurrency)))
    ticker_task = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # arm ticker before the first synchronous serialization
    start = time.perf_counter()
    for _ in range(rounds):
        await asyncio.gather(*(worker(i, True) for i in range(concurrency)))
    elapsed = time.perf_counter() - start
    stop.set()
    await ticker_task
    return {
        "concurrency": concurrency,
        "rounds": rounds,
        "operation_pairs": concurrency * rounds,
        "checkpoint_ms": summary(checkpoint_ms),
        "recovery_ms": summary(recovery_ms),
        "loop_lag_ms": summary(lag_ms),
        "loop_lag_max_ms": max(lag_ms),
        "pairs_per_second": concurrency * rounds / elapsed,
        "elapsed_seconds": elapsed,
        "body_bytes_per_put_or_get": len(encoded),
        "local_framed_file_bytes": len(encoded) + 65548,
    }


def environment() -> dict[str, Any]:
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "unknown",
    )
    return {
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "load_average": os.getloadavg(),
        "meminfo": {
            line.split(":")[0]: line.split(":")[1].strip()
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"))
        },
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("zstandard", "lz4", "rfc8785", "pydantic", "pydantic-ai", "anyio", "pillow")
        },
        "zstd_native": zstandard.ZSTD_VERSION,
        "lz4_native": lz4.library_version_string(),
        "filesystem": subprocess.check_output(["stat", "-f", "-c", "%T", str(ROOT)], text=True).strip(),
        "utc_started": datetime.now(UTC).isoformat(),
    }


async def run(args: argparse.Namespace) -> None:
    report = {"environment": environment(), "parameters": vars(args), "cases": []}
    rng = random.Random(args.seed)
    with tempfile.TemporaryDirectory(prefix="codec-benchmark-", dir=ROOT) as temporary:
        directory = Path(temporary)
        for profile in args.profiles:
            for size in args.sizes:
                print(f"{profile}/{size}", flush=True)
                state = make_state(profile, SIZES[size], args.seed)
                raw = canonical_model_bytes(state)
                assert canonical_model_bytes(decode_canonical_model(raw, ADAPTER)) == raw
                case = {
                    "profile": profile,
                    "size": size,
                    "target_content_bytes": SIZES[size],
                    "canonical_bytes": len(raw),
                    "canonical_sha256": hashlib.sha256(raw).hexdigest(),
                    "message_count": len(state.harness.message_history),
                    "canonical_encode": timed(lambda state=state: canonical_model_bytes(state), args.canonical_repeats),
                    "canonical_decode": timed(
                        lambda raw=raw: decode_canonical_model(raw, ADAPTER), args.canonical_repeats
                    ),
                    "codecs": {},
                }
                order = list(CODECS)
                rng.shuffle(order)
                case["codec_order"] = order
                for codec in order:
                    print(f"  {codec}", flush=True)
                    encoded = encode(raw, codec)
                    assert decode(encoded, codec, len(raw)) == raw
                    measurement = {
                        "encoded_bytes": len(encoded),
                        "encoded_fraction": len(encoded) / len(raw),
                        "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
                        "encode": timed(lambda codec=codec, raw=raw: encode(raw, codec), args.codec_repeats),
                        "decode": timed(
                            lambda codec=codec, encoded=encoded, raw=raw: decode(encoded, codec, len(raw)),
                            args.codec_repeats,
                        ),
                        "memory": codec_memory(raw, codec, directory),
                        "local": [],
                    }
                    for concurrency in args.concurrency:
                        measurement["local"].append(
                            await local_trial(
                                state,
                                codec,
                                concurrency,
                                args.rounds,
                                directory / f"{profile}-{size}-{codec}-{concurrency}",
                            )
                        )
                    case["codecs"][codec] = measurement
                report["cases"].append(case)
                Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
                gc.collect()
    report["utc_finished"] = datetime.now(UTC).isoformat()
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--sizes", nargs="+", choices=SIZES, default=["small", "medium", "large"])
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 8])
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--codec-repeats", type=int, default=25)
    parser.add_argument("--canonical-repeats", type=int, default=7)
    parser.add_argument("--seed", type=int, default=213)
    args = parser.parse_args()
    if min(args.rounds, args.codec_repeats, args.canonical_repeats, *args.concurrency) < 1:
        parser.error("sample counts and concurrency must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

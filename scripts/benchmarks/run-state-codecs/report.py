"""Render measured tables from raw samples; no inferred network speedups."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from codecs_bench import CODECS

HERE = Path(__file__).resolve().parent


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
            *["| " + " | ".join(map(str, row)) + " |" for row in rows],
        ]
    )


def main():
    reports = [json.loads((HERE / "results" / name).read_text()) for name in ("standard.json", "large-state.json")]
    cases = [case for report in reports for case in report["cases"]]
    environment = reports[0]["environment"]
    lines = [
        "# Run state codec measurements",
        "",
        "Generated from [standard.json](results/standard.json) and [large-state.json](results/large-state.json). See [methodology and limitations](README.md) before interpreting these numbers.",
        "",
        f"Base: `{environment['base_commit']}`. CPU: {environment['cpu']}. Python: {environment['python'].split()[0]}. Filesystem: {environment['filesystem']}. Dates: {reports[0]['environment']['utc_started']} to {reports[-1]['utc_finished']}.",
        "",
        "## Stored/transfer body sizes",
        "",
        "Exact canonical/encoded byte counts. Percent is encoded / canonical (lower is better), not percent saved. Bytes per object PUT or GET exclude transport and backend framing. The actual local file adds 65,548 bytes in every row.",
        "",
    ]
    rows = []
    for case in cases:
        row = [f"{case['profile']}/{case['size']}", f"{case['canonical_bytes']:,}"]
        row.extend(
            f"{case['codecs'][codec]['encoded_bytes']:,} ({case['codecs'][codec]['encoded_fraction']:.1%})"
            for codec in CODECS[1:]
        )
        rows.append(row)
    lines.extend(
        [
            table(["Case", "Raw bytes", "zstd-1", "zstd-3", "lz4-0"], rows),
            "",
            "## Canonical serialization and strict recovery",
            "",
            "Median wall ms, independently timed without object I/O or compression. Strict recovery includes parse, canonical re-encoding/comparison, and model validation, not just JSON parsing.",
            "",
        ]
    )
    lines.extend(
        [
            table(
                ["Case", "Messages", "Canonical encode ms", "Strict decode ms"],
                [
                    [
                        f"{case['profile']}/{case['size']}",
                        case["message_count"],
                        f"{case['canonical_encode']['wall_ms']['median']:.3f}",
                        f"{case['canonical_decode']['wall_ms']['median']:.3f}",
                    ]
                    for case in cases
                ],
            ),
            "",
            "## Codec-only cost for large states",
            "",
            "Fresh codec context per operation; checksums and frame content sizes enabled. Median wall / process CPU ms. Raw is an identity baseline, not another JSON serialization. No multi-threaded zstd compression.",
            "",
        ]
    )
    large = [case for case in cases if case["size"] in ("large", "xlarge")]
    rows = []
    for case in large:
        for codec in CODECS[1:]:
            measurement = case["codecs"][codec]
            row = [f"{case['profile']}/{case['size']}", codec]
            for operation in ("encode", "decode"):
                row.append(
                    f"{measurement[operation]['wall_ms']['median']:.3f} / {measurement[operation]['cpu_ms']['median']:.3f}"
                )
            rows.append(row)
    lines.extend(
        [
            table(["Case", "Codec", "Compress wall / CPU ms", "Decompress wall / CPU ms"], rows),
            "",
            "## Local storage-boundary latency for large states",
            "",
            "Measured boundary harness, **not full Service checkpoint/recovery**. Median / p95 ms. C1 = one worker; C8 = eight workers on independent keys. Only added compression is offloaded; existing canonical JSON work stays synchronous. Lag is maximum observed excess delay of a 5 ms ticker in the C8 trial. Standard: 7 samples at C1, 56 at C8; xlarge: 3 at C1, C8 not measured (resource-bounded stress run). Small-sample p95 is descriptive only (C1 p95 is the maximum).",
            "",
        ]
    )
    rows = []
    for case in large:
        for codec in CODECS:
            local = {trial["concurrency"]: trial for trial in case["codecs"][codec]["local"]}
            row = [f"{case['profile']}/{case['size']}", codec]
            for concurrency in (1, 8):
                for operation in ("checkpoint_ms", "recovery_ms"):
                    if concurrency in local:
                        stat = local[concurrency][operation]
                        row.append(f"{stat['median']:.1f} / {stat['p95']:.1f}")
                    else:
                        row.append("not measured")
            row.append(f"{local[8]['loop_lag_max_ms']:.1f}" if 8 in local else "not measured")
            rows.append(row)
    lines.extend(
        [
            table(["Case", "Codec", "C1 write", "C1 read", "C8 write", "C8 read", "C8 max lag"], rows),
            "",
            "## Codec process memory for large states",
            "",
            "Median of 3 fresh Linux processes per cell, MiB. Each cell is **total VmHWM / increase over pre-operation VmHWM**. Includes native allocations and retained output; baseline already includes the operation's input. Decode starts from an encoded file, not from compressing in that process. Does not measure whole-Service or concurrent peak memory. HWM increments can undercount allocations that fit below a previous import/input-load peak; this is not an allocator-exact workspace measurement. Full baseline and retained-RSS samples are in JSON.",
            "",
        ]
    )
    rows = []
    for case in large:
        for codec in CODECS:
            row = [f"{case['profile']}/{case['size']}", codec]
            for operation in ("encode", "decode"):
                samples = case["codecs"][codec]["memory"][operation]
                peak = statistics.median(sample["peak_rss_kib"] for sample in samples) / 1024
                increase = statistics.median(sample["hwm_increase_kib"] for sample in samples) / 1024
                row.append(f"{peak:.1f} / {increase:.1f}")
            rows.append(row)
    lines.extend([table(["Case", "Codec", "Compress total / extra MiB", "Decompress total / extra MiB"], rows), ""])
    (HERE / "RESULTS.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()

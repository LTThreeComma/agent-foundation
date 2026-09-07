"""Linux fresh-process codec RSS measurement, including native allocations."""

from __future__ import annotations

import gc
import json
import os
import sys
from pathlib import Path

from codecs_bench import decode, encode


def rss_kib() -> int:
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024


def hwm_kib() -> int:
    # Unlike getrusage.ru_maxrss, VmHWM resets on exec and does not inherit the
    # much larger benchmark parent's pre-exec high-water mark.
    return next(
        int(line.split()[1]) for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmHWM:")
    )


def main() -> None:
    path, codec, operation, size = sys.argv[1:]
    # Only the input is resident before measurement (not schema/fixture generation).
    # Decode receives an already encoded file: compression cannot inflate its baseline.
    data = Path(path).read_bytes()
    expected_size = int(size)
    gc.collect()
    baseline = rss_kib()
    before_hwm = hwm_kib()
    # Retain the output through the final reading. No warmup/context reuse.
    output = encode(data, codec) if operation == "encode" else decode(data, codec, expected_size)
    after = rss_kib()
    peak = hwm_kib()
    print(
        json.dumps(
            {
                "baseline_rss_kib": baseline,
                "before_hwm_kib": before_hwm,
                "peak_rss_kib": peak,
                "hwm_increase_kib": max(0, peak - before_hwm),
                "retained_rss_increase_kib": after - baseline,
                "output_bytes": len(output),
            }
        )
    )


if __name__ == "__main__":
    main()

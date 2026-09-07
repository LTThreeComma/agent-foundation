# Run state codec benchmark (#213)

A measurement-only branch for [issue #213](https://github.com/converge-ai-labs/agent-foundation/issues/213), especially the [request to compare zstd and lz4](https://github.com/converge-ai-labs/agent-foundation/issues/213#issuecomment-5572094519). No production storage code, dependency declarations, encoding marker, seal semantics, or accepted specifications are changed.

See [RESULTS.md](RESULTS.md) for measured tables. Raw samples, checksums, environment details, and per-operation CPU timings are in [results/standard.json](results/standard.json) and [results/large-state.json](results/large-state.json).

## Takeaways from this run

- **Content type matters more than state size alone.** For the ordinary large cases, zstd-1 retained 24.3% of repository-history JSON, 12.0% of structured tool-result JSON, 74.9% of inline-JPEG JSON, and 38.2% of CodeAct-values JSON. Corresponding lz4 fractions were 35.5%, 20.7%, approximately 100%, and 63.2%.
- **zstd-1 is a reasonable first integration candidate, not an established production choice.** It gave substantially smaller bodies than lz4 in these fixtures. zstd-3 helped the repository-history corpus but was slower and did not consistently improve size for the structured/float-heavy corpora. Compression levels are not a monotonic size guarantee on every input.
- **Images still merit measurement, but do not promise text-like ratios.** The large JPEG fixture shrank from 6,351,510 bytes to 4,759,329 with zstd-1; lz4 produced 6,351,338 bytes. Much of the zstd gain recovers base64 overhead rather than recompressing the already-compressed image format.
- **Serialization/validation dominates these local timings.** The 4,588,112-byte tool-results fixture needed approximately 591 ms canonical encoding and 554 ms strict recovery, versus approximately 6 ms zstd-1 compression and 2.5 ms decompression. The 3,831,922-byte CodeAct fixture needed approximately 750/710 ms for canonical encode/decode, versus approximately 14/4 ms for zstd-1. Compression alone neither removes that work nor fixes synchronous event-loop blocking.
- **Separate storage savings from latency claims.** Local boundary timings include substantial JSON, scheduling, and filesystem costs and show no reliable proportional speedup. Remote bandwidth savings remain an integration measurement, not a result of this benchmark. The larger image/CodeAct tables further expose codec CPU and decoded-memory cost; see the exact data rather than extrapolating from the ordinary cases.

## Reproduce

Linux, Python 3.13, and the source tree at the recorded commit are required. Start from the benchmark branch root:

```bash
uv sync --locked --all-packages
uv pip install -r scripts/benchmarks/run-state-codecs/requirements.txt

# Independent correctness checks; this optional benchmark is outside the normal
# scripts/tests suite and intentionally has no production dependency on lz4.
PYTHONPATH=scripts/benchmarks/run-state-codecs .venv/bin/pytest \
  scripts/benchmarks/run-state-codecs/test_benchmark.py -q

# 15 ordinary cases: five profiles, three content-size budgets.
.venv/bin/python scripts/benchmarks/run-state-codecs/benchmark.py \
  --output scripts/benchmarks/run-state-codecs/results/standard.json

# Two larger stress cases, fewer repetitions to bound runtime and resource use.
.venv/bin/python scripts/benchmarks/run-state-codecs/benchmark.py \
  --profiles image-jpeg codeact-values --sizes xlarge --concurrency 1 \
  --rounds 3 --codec-repeats 15 --canonical-repeats 3 \
  --output scripts/benchmarks/run-state-codecs/results/large-state.json

.venv/bin/python scripts/benchmarks/run-state-codecs/report.py
```

Use the explicit environment Python after installing the benchmark requirements: another workspace sync can remove the optional `lz4` installation. The codec pins are isolated in `requirements.txt`; the remaining dependencies come from the unchanged root `uv.lock`.

For a short smoke run, use `--sizes small --profiles image-jpeg codeact-values --rounds 1 --codec-repeats 2 --canonical-repeats 1 --output /tmp/codec-smoke.json`. Output is refreshed after every completed case; only a report with `utc_finished` is complete. Run the two real measurements sequentially, without other benchmarks or test suites competing for resources. The generator uses no network, model calls, production data, credentials, or user files. Temporary objects are created in an automatically cleaned temporary directory on the checkout filesystem, never in a configured Service store.

## Corpus and actual persistence representation

All samples are deterministic synthetic **schema-valid `RunStateEnvelope` progress checkpoints**, constructed using the actual Harness, Pydantic AI messages, effective Agent configuration, and Service canonical codec. Each case records its canonical SHA-256 and exact size. Timestamps and IDs are fixed; the random seed is 213. Actual canonical bytes are not committed because the generator is reproducible and no large fixture blobs are necessary.

The three ordinary content budgets are 16 KiB, 256 KiB, and 4 MiB. They are **not exact envelope sizes**: messages, JSON structure, metadata, JPEG chunk boundaries, and base64 expansion change the final size. Compare codecs only within the same case. The larger budget is 32 MiB. Exact serialized sizes, rather than budget labels, are in the results.

| Profile              | Content and relevance                                                                                                                                   | Important limitation                                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `repository-history` | Shuffled, distinct excerpts from tracked Service Python source, `docs/`, and `spec/`, wrapped in prompt/response pairs                                  | Code/document text, not a real conversation trace; no repeated corpus to fill the tested size                             |
| `tool-results`       | Tool call/return pairs with record IDs, paths, status, timing, and varied English/Unicode summaries                                                     | Synthetic structured results with deliberately repeated field names                                                       |
| `entropy`            | Distinct seeded random bytes stored as base64 strings in tool results                                                                                   | A low-compressibility JSON control; base64 still has recoverable encoding overhead                                        |
| `image-jpeg`         | Unique noisy RGB images encoded as real JPEG, then passed as `BinaryContent` through actual message serialization                                       | Already-compressed synthetic image bytes, not representative photographs or screenshots; no repeated identical image blob |
| `codeact-values`     | Actual `CodeActStoredValues`, version 1 under the `a13n.codeact` Capability namespace, containing record IDs, random float embeddings, scores, and tags | JSON values only, not Monty heap, pickle, or interpreter snapshots                                                        |

**Large state does not automatically imply high compressibility.** Inline images occupy base64 in canonical JSON; zstd can recover some base64 overhead even when the underlying JPEG has already been compressed. This is different from recompressing the JPEG pixels, and different from `ImageUrl`/asset-reference messages, which store a reference rather than image bytes. We do not infer URL-backed image state size from the inline-image measurements.

Current `CodeActConfig.max_state_bytes` defaults to **10 MiB**. Ordinary CodeAct cases are below that limit. The 32 MiB-budget CodeAct case is a **configuration-raised stress case**, not accepted by default `store()` settings; a 64 MiB state allowance would cover it. The benchmark constructs the portable schema directly and does not silently change runtime configuration. The Service's separate decoded Run-state limit is 256 MiB; all these complete envelopes remain below it. Compression must not be used to bypass either logical limit.

## Codecs

- `raw`: exact RFC 8785 canonical JSON bytes; codec operations are identity functions with no copy.
- `zstd-1` and `zstd-3`: single-threaded zstandard, level 1 or 3, frame checksum and content size enabled.
- `lz4-0`: lz4 frame default compression level 0, linked 64 KiB blocks, content checksum and size enabled.
- Fresh compressor/decompressor context per operation, no dictionary and no cross-checkpoint history. Reusing contexts could change CPU/allocation results.
- All codecs see identical canonical bytes. Every encode/decode round trip verifies exact equality; sample generation and codec timing are separate.
- `codecs_bench.py` is a benchmark utility, **not a hardened production decoder or an accepted encoding contract**. Correctness tests exercise round trips, checksums, truncation, trailing bytes, and expected decoded length, not the complete adversarial resource-bound matrix.

## What the timing includes

1. **Canonical codec:** actual `canonical_model_bytes()` and `decode_canonical_model()` are independently timed. Strict recovery includes duplicate-key checks, canonical re-encoding/comparison, and full model validation. Replacing that with plain `json.loads()` would understate the baseline.
2. **Compression codec:** one warmup, then 25 wall/process-CPU samples per operation (15 for xlarge). Serialization and I/O are excluded. A raw identity timing is not a raw checkpoint timing. Context construction and checksums are included.
3. **Local storage boundary:** real `LocalObjectStore`, conditional replacement, encoded SHA-256, full GET, digest verification, decompression, and strict canonical recovery. Seven synchronized rounds after one warmup at concurrency 1 and 8 (xlarge: three rounds at concurrency 1 only). Workers use independent keys and the same immutable fixture; every PUT replaces a complete body. Codec order is seeded and shuffled per case.
4. **Event loop:** a 5 ms asyncio ticker records excess scheduling delay during the measured local trial. Added compression/decompression runs via `anyio.to_thread.run_sync`; current synchronous canonical encoding/validation remains on the event loop for every codec, including raw. This tests the impact of adding codec offload alone, not an optimized whole-pipeline implementation.
5. **Memory:** three fresh Linux subprocesses per codec/direction, importing only the codec utility. `/proc/self/status` `VmHWM` captures Python and native peak resident memory; baseline, increase, and retained RSS are also recorded. `/proc` is intentional: `getrusage().ru_maxrss` can inherit the much larger parent's pre-exec high-water mark. Decode starts with a pre-encoded file, so encode allocations do not pollute its baseline. This is single-operation codec memory, not full Service/concurrent peak memory.

The local benchmark is a **storage-boundary harness**, not execution through `RunStateStore` or the complete Service. It deliberately does not choose compression marker/content type, alter writer/seal identity, or implement a parallel production store. Repeated writes measure full-body replacement at one key without claiming to advance real Run checkpoint sequences.

## Interpretation limits

- Measured on one shared development machine, not an isolated performance runner. CPU scheduling, thermal state, background load, allocator reuse, and filesystem cache affect wall times. Machine/load details are recorded. Do not interpret small percentage differences as reliable wins; repeat on deployment hardware.
- C1 has seven samples (three for stress); C8 has 56 for ordinary cases and was not completed for stress. The initially started C8 stress run was stopped to bound shared-machine resource use; its partial samples are not reported. Nearest-rank p95 is descriptive, not a stable tail/SLO estimate. Samples from synchronized rounds are correlated, not independent user arrivals.
- `LocalObjectStore` adds **65,548 bytes** of framing per file, uses a shared publication lock, and performs atomic replacement without `fsync`. Results are warm-cache local filesystem timings, not power-loss durability or remote object storage latency. The reported file length is logical length, not physical disk allocation; filesystem compression/cache is not controlled.
- Encoded size is the object body transferred for one PUT/GET. No network bytes were captured; HTTP/TLS/S3 framing, bandwidth, RTT, and request pricing are excluded. Codec ratios do not reduce object request counts. No S3, MinIO, network-shaping model, or inferred end-to-end speedup is presented as measured evidence.
- No database, scheduler, model execution, export-state traversal, writer-claim reconciliation, stale-writer fencing, sealing, waiting feedback, parent continuation, or crash recovery is exercised by the timed harness. Existing tests remain relevant, but compression-specific acceptance evidence for those paths is still needed.
- The 32 MiB stress cases are important resource probes but not evidence that arbitrary huge states are cheap to decode. The complete canonical/schema work still happens after decompression, and additional decoded allocations remain necessary.
- This branch supports choosing a candidate codec for further integration work; it does **not** settle issue #213's storage format, logical-vs-encoded digest semantics, exact-byte reuse, compatibility, or recovery contract.

## Source anchors

- [RunStateEnvelope](../../../packages/a13n-service/a13n_service/interactions/state.py)
- [Canonical codec](../../../packages/a13n-service/a13n_service/storage/codec.py)
- [Current RunStateStore](../../../packages/a13n-service/a13n_service/interactions/objects.py)
- [LocalObjectStore](../../../packages/a13n-service/a13n_service/storage/object_store/local.py)
- [Portable Harness state](../../../packages/a13n-harness/a13n_harness/state.py)
- [CodeAct stored values](../../../packages/a13n-harness/a13n_harness/toolsets/codeact_state.py)
- [CodeAct defaults](../../../packages/a13n-harness/a13n_harness/codeact/config.py)

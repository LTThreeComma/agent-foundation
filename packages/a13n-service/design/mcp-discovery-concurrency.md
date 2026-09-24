# MCP discovery concurrency

Status: proposed optimization for `codex/a13n-service-rewrite`; this document changes no runtime behavior. It records the requested case and plan for review, outside the accepted specifications.

## Case and expected benefit

An Agent selects GitHub, Slack, and Notion MCP connections. Before the model can use their tools, the runtime needs the applicable tool definitions. Independent remote discovery requests should overlap, within a bounded budget, instead of making one connection wait for another's response.

Only the connections selected for the executing Agent are in scope, not every MCP server configured in the workspace. Tool selection narrows the exposed definitions; it does not necessarily narrow the server's `tools/list` response. MCP pagination may require several requests for one catalogue.

For illustration, if three independent discoveries take 200, 300, and 400 ms, sequential discovery takes about 900 ms. With three available discovery slots, overlapping them takes about 400 ms plus coordination overhead. These are arithmetic examples, not measurements. They exclude any setup that remains serial, and a single connection has no comparable speedup.

`await` lets other tasks on the Worker run while a request waits. It does not release the Attempt's admission slot or make successive iterations of a loop concurrent. Discovery child tasks remain part of that Attempt; they do not create more Runs or RunAttempts.

## Evidence and rewrite differences

The original case was observed in the pre-rewrite implementation at [revision `1fa04257`](https://github.com/converge-ai-labs/agent-foundation/tree/1fa04257d3a8ba123fecf6725f06371eb19dbdf7):

- [`ExternalToolRuntime._capabilities()`](https://github.com/converge-ai-labs/agent-foundation/blob/1fa04257d3a8ba123fecf6725f06371eb19dbdf7/packages/a13n-service/a13n_service/connectivity/execution.py#L249) enters selected connection contexts sequentially; an explicitly empty tool selection is skipped.
- [`_mcp()`](https://github.com/converge-ai-labs/agent-foundation/blob/1fa04257d3a8ba123fecf6725f06371eb19dbdf7/packages/a13n-service/a13n_service/connectivity/execution.py#L389) opens a fresh client/toolset and awaits `list_tools()` before yielding the capability. This path has no cross-Attempt discovery cache; even `defer_loading` follows this explicit discovery step.
- Its connection contexts remain alive for tool execution. Parallelizing context entry alone is not sufficient to manage their later cleanup correctly.

The rewrite baseline reviewed here is [`16447e8c`](https://github.com/converge-ai-labs/agent-foundation/tree/16447e8ce566cf0e952d8dcb22eed606d58784f3). It has a different runtime:

| Owner                                                                                                        | Behavior in the rewrite baseline                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`resources/connections/runtime.py`](../a13n_service/resources/connections/runtime.py), `open_connections()` | Opens HTTP clients and builds MCP capabilities in a loop, but does not explicitly call MCP `list_tools()` there. The connector branch uses a Redis discovery cache and lists tools on a miss. |
| [`providers/tools/mcp.py`](../a13n_service/providers/tools/mcp.py), `mcp_capability()`                       | Supplies a `ContextualMCP` recipe with a fresh local toolset factory, selected tools, caller headers, and `defer_loading`. Its `MCPToolset` enables `cache_tools=True`.                       |
| [`Harness ContextualMCP.for_run()`](../../a13n-harness/a13n_harness/mcp.py)                                  | Resolves headers and creates the replacement capability once per logical Harness run.                                                                                                         |
| [`resources/connections/discovery.py`](../a13n_service/resources/connections/discovery.py)                   | Provides cached discovery for the management API and connector runtime. That cache does not itself establish a cross-Run cache for the runtime MCP toolsets.                                  |

The baseline's `uv.lock` pins `pydantic-ai-slim` 2.48.0. Inspecting that installed dependency reveals an existing concurrency boundary:

- `pydantic_ai/toolsets/combined.py`: `CombinedToolset.get_tools()` gathers child `get_tools()` calls concurrently and merges results in input order. Its `__aenter__()` still enters child toolset contexts sequentially.
- `pydantic_ai/mcp.py`: `MCPToolset.get_tools()` calls `list_tools()`. With caching enabled, the catalogue is reused until a tools-list change notification or the final toolset exit invalidates it.

Thus the original serial-loop evidence does not prove serial catalogue requests in the rewrite. The optimization may instead concern remaining serial connection setup and bounding discovery that already overlaps. Record actual request overlap through the full Harness path, including deferred loading, before choosing the implementation point. Retain upstream concurrency rather than add a second discovery pass. Source inspection here is not an end-to-end latency measurement.

## Proposed behavior

### Scope and admission

- Overlap independent discovery for the selected MCP connections at the lifecycle boundary that actually owns discovery. Do not eagerly discover deferred capabilities solely to make startup appear faster.
- Keep cursor-dependent pages within one catalogue sequential. Apply the existing catalogue size and tool-count limits to the complete result.
- Bound both per-Attempt discovery and aggregate discovery within a Worker process. A per-Attempt limit of four is a benchmark starting point, not a decided deployment default: eight Attempts could otherwise create 32 simultaneous discoveries. The Worker budget also covers inline child-Agent discovery.
- Acquire discovery capacity before starting remote setup/discovery, and release it on completion, error, or cancellation. A retained session does not retain a discovery permit for the entire Run. Waiting for capacity is cancellable and counts toward the preparation deadline.
- Keep resolved inputs detached from database sessions. No session or transaction remains open across remote I/O, task waits, or the lifetime of an MCP connection.

### Connection ownership and assembly

Use the existing Harness and upstream toolset lifecycle as the first implementation option. Separate independent discovery work from resource ownership instead of wrapping `AsyncExitStack.enter_async_context()` calls in an unbounded `gather()`.

If connection establishment also needs to overlap, each owner task must enter, retain, and exit its own connection context. It publishes readiness to the assembler, remains supervised while the Harness uses the connection, and closes the context when execution ends. Do not open a task-group/cancel-scope context in one task and close it in another. Prefer the existing upstream lifecycle if it can provide these guarantees; do not introduce a general connection manager for this optimization.

Collect capabilities by their original selection index. Completion order must not change tool names, source identity, capability order, filtering, or dispatch ownership. Keep successful sessions usable until Harness execution and its tools have stopped; do not discover, close, and then reconnect just to run the tools.

### Failure and authorization

- A required connection's preparation failure fails preparation. Cancel other unfinished preparation tasks and close successfully opened resources; do not silently run with fewer tools. Deferred discovery retains its existing failure boundary.
- Preserve existing endpoint restrictions, credential handling, caller headers, schema validation, tool filters, and live dispatch authorization. Discovery does not grant authority to call a tool later.
- Keep per-operation timeouts and bound the whole preparation phase, including capacity waits. Cleanup is bounded and must still run when the parent Attempt is cancelled or loses authority.
- Identify the failed connection using safe identifiers and classified errors. Do not place tokens, headers, or upstream response bodies into diagnostics.
- Preserve failure classification when a task group produces grouped exceptions. Parallel discovery must not turn a known authorization or timeout failure into an unrelated generic error.

### Cache boundary

This proposal changes concurrency, not catalogue freshness or authentication semantics. Preserve existing management/connector caches and per-toolset caches. It neither shares live MCP sessions between Runs nor introduces a cross-Run MCP catalogue cache.

A separate cache proposal would need to account for endpoint, workspace/connection identity, credential and configuration changes, and run-specific caller headers that can alter the visible tool surface. The management cache must not be assumed safe for a differently scoped runtime request.

## Implementation and validation plan

1. Trace the rewrite's actual discovery boundary with controlled MCP endpoints and the repository's pinned dependencies. Record initialization and `tools/list` counts, request overlap, first model-request time, repeat model requests, and the effect of `defer_loading`. Distinguish connection setup, discovery, and any remaining serial work.
2. Add bounded overlap only where independent remote operations are still serial. Keep the change at the owning Service or Harness boundary, retain public upstream primitives, and avoid duplicate discovery. Choose limits from the measured Worker resource budget before wiring a production default.
3. Validate the invariants below before comparing latency. Use event barriers to prove overlap and limits; avoid flaky tests that only assert a tight wall-clock threshold.
4. Compare sequential and bounded-concurrent behavior using the same fixtures, credentials, selected tools, and cache state. Report measured p50/p95 and error rates separately from the illustrative 900/400 ms calculation. Run at least 30 samples per benchmark variant; include concurrent Attempt startup and record actual discovery concurrency. No production telemetry is added by this documentation change.

| Scenario                                                            | Required evidence                                                                                                                                             |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| One selected connection                                             | Same tools, dispatch path, and session lifetime; no extra discovery or reconnect introduced.                                                                  |
| Three independent connections with controlled delays                | Requests overlap when capacity allows; exposed tools and first model input match the sequential baseline.                                                     |
| More connections than the per-Attempt limit                         | Peak discovery stays within the configured bound and queued work progresses.                                                                                  |
| Several Attempts and inline children                                | Worker-wide discovery bound holds; cancellation returns permits for other work.                                                                               |
| Different completion orders                                         | Deterministic capability order, source identifiers, aliases, and tool filtering.                                                                              |
| Failure, timeout, or cancellation before and after readiness        | No partial required tool surface, leaked session, stuck child task, or leaked permit. Known failures retain their classification.                             |
| Real MCP transport lifecycle through a local test server            | Contexts enter and exit under compatible task ownership; tools can still be called after discovery; shutdown closes resources. Mocks alone do not prove this. |
| Credential/caller-header differences and live disablement           | No cross-Run tool or credential leakage; dispatch checks still reject revoked access.                                                                         |
| Deferred tools, pagination, repeated model requests, fresh Attempts | Preserve loading and freshness behavior, validate bounded catalogues, and avoid additional discovery caused by the optimization.                              |

The expected outcome is lower multi-connection preparation latency where serial discovery is confirmed, with the same tool surface and execution authority. Request count, Run admission capacity, and checkpoint semantics are unchanged.

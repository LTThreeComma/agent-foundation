# Daemon Lifecycle and Configuration

## Design Position

`agent-envd` is one authority-bearing daemon for one user, one Environment identity, and one process generation. It validates trusted bootstrap configuration, creates fresh generation-private runtime state, initializes resource owners and command isolation, and admits EIP only after every required enforcement component is usable.

A daemon uses either trusted stdio or outbound reverse WebSocket. In the network profile envd is a dialer: it actively connects to a trusted control-service listener and never binds an inbound EIP, HTTP, health, or readiness endpoint.

Bootstrap configuration is operator or provider-adapter input. EIP requests can use only configured mounts, methods, profiles, limits, and isolation posture. They cannot change Environment identity, native roots, carrier endpoint/credentials, hard quotas, isolation mode, protected paths, or daemon generation.

## Boundaries

| Concern                                                                                                                | Owner                                                    | Relationship                                  |
| ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------------------------- |
| Provider resource creation, lifecycle credential, endpoint routing, and envd attachment credential issuance            | Host provider adapter                                    | Completes trusted bootstrap and refresh       |
| Envd executable, configuration, private bootstrap channel, and process lifecycle                                       | Operator or provider adapter                             | Launches envd inside the selected Environment |
| Configuration validation, generation, resource owners, isolation probe, local readiness, connector, drain, and cleanup | `agent-envd`                                             | One daemon lifecycle                          |
| Carrier framing, reverse-WebSocket handshake/reconnect, and EIP session                                                | [Transports and Sessions](03-transports-and-sessions.md) | Begins only after local readiness             |
| Harness run and durable execution lifecycle                                                                            | Harness and Host                                         | Independent of daemon process lifetime        |

One ready daemon can serve sequential stdio work or a sequence of reconnecting reverse-WebSocket sessions and can own concurrent generation resources within daemon-global ceilings. A session is a protocol carrier, not a tenant, principal, or run. Another user or mutually untrusted workload requires another daemon instance, runtime root, bootstrap binding, and provider resource boundary.

## Trusted Configuration

The following conceptual schema defines stable configuration classes. It is not the CLI parser or EIP wire shape.

```python
type TransportMode = Literal["stdio", "reverse_websocket"]
type ExecutionIsolationMode = Literal["required", "disabled"]
type ExecutionNetworkMode = Literal["host", "deny"]


class AttachmentCredentialProvider(BaseModel):
    bootstrap_channel: SecretBootstrapChannel


class ReverseWebSocketConfig(BaseModel):
    endpoint: str
    subprotocol_major_versions: tuple[int, ...] = (1,)
    credential_provider: AttachmentCredentialProvider
    tls_trust_roots: tuple[str, ...] = ()


class DaemonLimits(BaseModel):
    max_request_bytes: int
    max_response_bytes: int
    max_transfer_frame_bytes: int
    max_concurrent_operations: int
    max_concurrent_file_transfers: int
    max_file_transfer_bytes: int
    max_processes: int
    max_operation_duration_ms: int
    max_output_preview_bytes: int
    max_output_bytes_per_stream: int
    max_spool_bytes: int


class DaemonConfig(BaseModel):
    environment_id: str
    runtime_directory: str
    transport: TransportMode
    reverse_websocket: ReverseWebSocketConfig | None
    root_mount_id: str | None
    mounts: tuple[TrustedMountConfig, ...]
    executable_search_roots: tuple[str, ...]
    shell_profiles: tuple[TrustedShellProfile, ...]
    limits: DaemonLimits
    execution_isolation: ExecutionIsolationMode = "required"
    execution_network: ExecutionNetworkMode = "host"
    execution_extra_read_only_paths: tuple[str, ...] = ()
    payload_uid: int | None = None
    payload_gid: int | None = None
```

All numeric limits are positive and finite. Connection, initialization, liveness, reconnect, transfer, and shutdown timing is also finite internal policy owned by the relevant lifecycle or transport contract rather than a separate compatibility surface. `max_spool_bytes` is at least twice `max_output_bytes_per_stream`, so one command can reserve both streams. `max_response_bytes` leaves room for both base64-encoded stream previews and the largest valid command-result envelope at `max_output_preview_bytes`. The wire-visible `EIPLimits` contains only values a client needs to construct work and therefore omits the daemon-wide spool ceiling. Envd also bounds session, operation-record, process-record, transfer, staging, spool-record, queue, and shutdown resources internally; those implementation limits are not separate protocol features. Exhaustion returns `busy` or `quota_exceeded` before unsafe allocation.

`max_output_bytes_per_stream` is reserved independently for stdout and stderr before one command starts, and `max_output_preview_bytes` bounds each stream preview. `max_spool_bytes` is the finite daemon-wide spool disk ceiling. Existing output and process records are not evicted to admit new work; callers reclaim them explicitly.

## Configured Mounts and Executables

`TrustedMountConfig` is owned by [Resource Operations](04-resource-operations.md). Every native root is operator-provided, canonicalized, checked against protected paths, and opened or fixed through the strongest platform-relative authority before local readiness. The provider keeps the containing directory stable during bootstrap. Envd never synthesizes a server-filesystem view and never accepts a root from EIP.

`root_mount_id` is optional with zero mounts, can be inferred only when exactly one mount exists, and is required with multiple mounts when Environment-relative routing needs a default. It must identify exactly one configured mount.

A deliberate whole-filesystem configuration uses ordinary mounts, for example `/` on POSIX or explicit Windows volume roots. Such a root can physically contain the generation runtime, so startup admits it only when the EIP resource resolver can subtract every protected runtime root with capability-relative, no-follow enforcement. Required command isolation independently subtracts the same roots from payload authority and proves that projection in its production probe. Failure of either layer fails startup rather than exposing daemon state or silently narrowing the configured mount.

Executable search roots and shell profiles are trusted canonical configuration defined by [Command and Process Execution](05-command-and-process-execution.md). Typed executable paths resolve through configured mounts. Models and ordinary request fields cannot add search roots, native paths, shell helpers, or platform capabilities.

## Configuration Sources and Secrets

The executable accepts trusted configuration from an operator-selected file, explicit non-secret CLI values, documented process environment, and a provider-created private bootstrap channel. One effective immutable value is computed before owner initialization. Conflicting duplicate authority-bearing values fail startup.

Stable non-secret environment configuration includes:

| Variable                                                | Values/requirement                              | Meaning                                           |
| ------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------- |
| `AGENT_ENVD_ENVIRONMENT_ID`                             | Required                                        | Stable provider Environment identity              |
| `AGENT_ENVD_TRANSPORT`                                  | `stdio` or `reverse_websocket`; default `stdio` | Selects the carrier profile                       |
| `AGENT_ENVD_REVERSE_WS_URL`                             | Required only for reverse WebSocket             | Normalized outbound `wss://` endpoint             |
| `AGENT_ENVD_RUNTIME_DIR`                                | Required absolute path                          | Parent for fresh generation-private runtime state |
| `AGENT_ENVD_EXECUTION_ISOLATION`                        | `required` or `disabled`; default `required`    | Selects envd inner command isolation              |
| `AGENT_ENVD_EXECUTION_NETWORK`                          | `host` or `deny`; default `host`                | Selects required-backend network ceiling          |
| `AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS`            | JSON array; default `[]`                        | Adds trusted command runtime roots                |
| `AGENT_ENVD_EXECUTION_UID` / `AGENT_ENVD_EXECUTION_GID` | Optional paired positive Linux IDs              | Selects trusted final Linux payload identity      |

The runtime parent is required on every platform and carrier profile because generation-private spool, control, probe, and connector state is unconditional. Envd does not derive an authority-bearing parent from the ambient current directory, user home, or platform temporary-directory environment. The provider creates and protects the parent before launch; envd creates a fresh unpredictable generation child and validates ownership, permissions or ACLs, and no-link/reparse shape before use.

Reverse WebSocket requires a provider-owned short-lived attachment credential source on a protected bootstrap channel. The channel can be an inherited private descriptor/handle, service-manager secret facility with refresh IPC, or equivalent provider mechanism. Its platform representation is not EIP and is never caller-selectable. A one-shot secret source is valid only when the provider explicitly treats expiry/authentication failure as generation-fatal and restarts envd with a fresh generation.

Secrets are not accepted through CLI arguments or endpoint URLs. Attachment credentials, lifecycle credentials, bootstrap-channel identities, and their digests are absent from EIP, descriptors, readiness, argv, normal process environment, command environment, logs, metrics, traces, and errors. Envd keeps only the current and, during atomic refresh, next credential in protected memory and discards expired values.

`AGENT_ENVD_*` names are reserved and cannot be set or unset by a command request. Child environments are rebuilt rather than inheriting the daemon environment wholesale.

## Reverse-WebSocket Endpoint Policy

The configured URL must be `wss://`, contain no user information, fragment, or secret query component, and normalize to one fixed endpoint. Envd performs ordinary certificate-chain and hostname validation against platform roots plus optional explicit operator trust roots. It never follows redirects, disables verification, downgrades to plaintext, or accepts caller-controlled forwarding metadata as identity.

The connector presents the short-lived attachment credential as an `Authorization: Bearer` upgrade header and offers supported `eip.v<major>` subprotocols. The control service must select exactly one offered protocol. Authentication binds the attachment to the provider's expected Environment before the EIP requester sends `initialize`.

Invalid TLS, hostname, endpoint policy, redirect, or subprotocol is non-recoverable for the generation. Authentication failure requests credential refresh when available; a provider-classified nonrefreshable failure is generation-fatal. Transient DNS, connect, remote-unavailable, and liveness failures use capped exponential backoff with full jitter as owned by [Transports and Sessions](03-transports-and-sessions.md).

## Environment Identity and Generation

```python
class EnvironmentIdentity(BaseModel):
    environment_id: str
    generation: int
```

`environment_id` is mandatory trusted provider input. The provider supplies the same expected value to its control service/requester. Envd never invents an identity and asks the peer to trust it afterward.

Each daemon start creates a fresh unpredictable nonzero unsigned 64-bit `generation`. It is an equality fence for volatile runtime state, not a monotonic counter, recovery marker, credential, or ordering signal. Reconnect does not change generation; restart always does.

Operation records and receipts, process handles, output references, file transfer handles, and private spool data never survive restart. Reader/writer handles also end with their exact session. Native files remain provider Environment state.

## Generation-Private Runtime State

The trusted runtime parent is dedicated to one Environment's envd lifecycle and contains no provider or user data. Before inspecting or changing children, envd acquires one platform-native exclusive, non-inherited lifetime lock for that parent. Failure to acquire the lock means another generation may still own it and fails startup.

While holding the lock and before creating a new generation, envd uses capability-relative, no-follow operations to enumerate the parent's immediate entries. Apart from the stable lock object, every entry must match envd's private generation-directory format, daemon identity, ownership, and non-link/reparse shape. Envd removes each validated crash-left generation tree recursively without following links. An unexpected entry, uncertain ownership or shape, traversal escape, or deletion that cannot be proven complete fails startup before local readiness. It never ignores or merely stops accounting for stale spool bytes.

Only after stale-state cleanup succeeds does envd create one fresh unpredictable generation subtree beneath the trusted runtime parent. It never reuses fixed prior-generation command-home, command-temp, spool, control, or probe contents. A normal shutdown removes the current subtree while still holding the parent lock; process or Host failure releases the native lock so the next start can perform the same verified cleanup.

The subtree contains only envd-owned classes:

- private command home and temporary roots;
- append-only command-output spool files and metadata;
- isolation control/probe state;
- internal connector and supervisor state;
- bounded cleanup evidence.

It is outside EIP mount authority and required-isolation command grants except the exact command-home/temp projection intended for one command policy. This is an authorization property, not a claim that the native path is physically disjoint from a deliberately broad mount. Every EIP path resolution and traversal subtracts the opened protected runtime roots before access; list, find, search, and recursive mutation refuse a protected entry before descending or returning its contents. Required command isolation performs its own subtraction. Native permissions or ACLs are restrictive defense in depth. Runtime startup fails if freshness, ownership, non-link/reparse shape, capacity policy, or either protected-path boundary cannot be established.

Destination-local mutation candidates remain beside their target because publication must stay on the destination filesystem. They are not private spool objects and can be visible to another actor that already controls that directory.

## Startup State Machine

```mermaid
stateDiagram-v2
    [*] --> Starting
    Starting --> LocallyReady: config, owners, runtime, and required isolation probe succeed
    Starting --> Failed: required startup step fails
    LocallyReady --> CarrierConnecting: reverse WebSocket selected
    LocallyReady --> Serving: trusted stdio initialize succeeds
    CarrierConnecting --> Serving: WebSocket upgrade and initialize succeed
    CarrierConnecting --> CarrierConnecting: recoverable reconnect
    CarrierConnecting --> Draining: generation-fatal attachment failure
    Serving --> CarrierConnecting: reverse-WebSocket carrier loss
    Serving --> Draining: shutdown or fatal ownership fault
    LocallyReady --> Draining: stdio parent loss
    Draining --> Stopped: bounded cleanup succeeds
    Draining --> Failed: cleanup remains uncertain
    Failed --> [*]
    Stopped --> [*]
```

Startup order is:

1. Parse trusted sources and reject unknown, conflicting, malformed, or unsafe authority configuration.
2. Validate Environment identity and create a fresh generation.
3. Create and validate the generation-private runtime subtree.
4. Canonicalize configured mounts, protected paths, executables, and shell profiles.
5. Create the operation ledger, transfer/process records, and command-output spool.
6. Initialize the selected execution backend.
7. In `required` mode, run the native production probe for Linux, macOS, or Windows.
8. Reserve stdio framing, or initialize the outbound connector and credential source.
9. Publish local readiness to the trusted provider lifecycle boundary and begin carrier admission.

No stdio frame is accepted and no reverse-WebSocket attempt begins before the required isolation probe succeeds. Envd never binds an inbound listening socket for EIP.

## Readiness

Readiness has two distinct facts:

- **local readiness**: configuration, generation-private state, mounts, owners, and isolation probe are usable;
- **carrier readiness**: one current trusted carrier has completed EIP initialization for the expected Environment and generation.

In stdio mode, successful `initialize` is the usable readiness boundary. Stdout contains only framed EIP messages; startup diagnostics use stderr and process exit.

In reverse-WebSocket mode, the provider observes local readiness through the same trusted launch/bootstrap control boundary that owns envd, while the control service observes carrier readiness from the initialized connection. There is no readiness JSON line to discover a listener and no `/healthz` or `/readyz` route. Consumers dispatch only after carrier readiness. Losing carrier readiness leaves local generation-owned resources intact while reconnect is recoverable.

Readiness never contains credentials, native roots, protected paths, helper locations, command content, or private runtime names.

## Admission and Runtime Ownership

Admission is bounded at daemon-global request/operation/session level and at method-specific transfer, staging, process, output, payload, and platform resource level. Capacity is reserved before native allocation or dispatch.

Generation state has four clear domains:

- a session contains initialization and session-scoped file transfers;
- one operation ledger contains running requests and retained terminal evidence/receipts for effectful methods; completed observation entries are removed after response handoff;
- the command manager contains owned process trees and process records;
- the output spool contains stdout/stderr records and disk files.

Filesystem candidates remain in the file-transfer or mutation domain. These domains can coordinate one handoff, such as a sealed writer becoming commit-owned, without introducing generic ownership tokens or overlapping registries.

Operation admission inserts its ledger entry synchronously before handler work can run. The ledger's observable replay and cancellation behavior is owned by [EIP Protocol](02-eip-protocol.md#operation-ledger-and-cancellation).

A command reserves process and output capacity before payload release. Output appends to private files and keeps only bounded previews in memory. Process and output records remain until explicit release or generation end; finite capacity rejects later starts instead of silently reclaiming valid handles.

Session close removes only session-owned transfers. Accepted operations, processes, receipts, and output remain generation-owned. A peer that violates bounded transfer or correlation state loses the carrier rather than forcing unbounded bookkeeping.

## Draining and Shutdown

Shutdown begins from an operator signal, stdio parent loss, explicit provider lifecycle action outside EIP, generation-fatal reverse-WebSocket state, or unrecoverable ownership fault. EIP has no daemon-shutdown method.

Envd stops new admission, closes session transfers, asks accepted foreground work to cancel, and lets already owned mutations publish their strongest terminal evidence within a finite drain budget. It closes process stdin, applies the active backend's strongest cleanup to every command tree, waits for bounded cleanup evidence, removes generation-private state, closes carrier/bootstrap channels, and exits.

No process is contractually allowed to outlive envd shutdown. Required Linux namespace and Windows Job cleanup normally prove complete tree teardown. macOS can report a residual only while inherited Seatbelt confinement remains proven. Disabled mode relies on outer-Host teardown for authority outside envd's native target.

If required cleanup cannot be proven, envd exits nonzero with safe supervisor diagnostics and never reports normal stopped completion. A provider can then destroy the outer Environment boundary.

## Observability

Structured logs and metrics describe control-plane facts without copying request, file, or output content by default.

Safe dimensions include daemon version, EIP major, transport profile, lifecycle state, approved Environment correlation, connector/reconnect outcome class, isolation backend/probe class, method name, operation stage/outcome/duration, byte counts, active owner counts, quota denials, process termination, and cleanup outcome.

Observability excludes attachment credentials, authorization headers, bootstrap-channel identifiers, operation IDs unless explicitly approved for secure diagnostics, transfer handles, full commands, request environments, native paths, ACL/profile source, file/output content, and provider lifecycle credentials.

The EIP descriptor exposes only non-secret client-actionable limits, exact available methods, configured logical mounts, generation, and envd isolation posture. It never infers outer provider security.

## Failure Semantics

| Failure                                                                  | State and observable result                                               |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| Invalid/conflicting configuration                                        | Exit nonzero before local readiness                                       |
| Runtime parent cannot be locked or stale generation cleanup is uncertain | Exit nonzero before local readiness                                       |
| Runtime subtree cannot be created fresh and private                      | Exit nonzero before admission                                             |
| Required isolation backend/probe fails                                   | Exit nonzero; no carrier admission or fallback                            |
| Stdio framing setup fails                                                | Exit nonzero before initialization                                        |
| Reverse-WebSocket endpoint/TLS/subprotocol is invalid                    | Generation-fatal drain and nonzero exit                                   |
| Attachment credential expires or is rejected                             | Refresh and reconnect, or generation-fatal when nonrefreshable            |
| Transient DNS/connect/liveness failure                                   | Capped jittered reconnect; generation-owned state remains                 |
| Runtime admission exhausted                                              | Typed pre-dispatch `busy`; no native work                                 |
| Transfer/staging/spool quota exhausted                                   | Typed `busy` or `quota_exceeded`; no unbounded allocation                 |
| Candidate/spool cleanup is transiently uncertain                         | Conservative charge plus bounded retry; safe unaffected work can continue |
| Cleanup uncertainty crosses safety threshold                             | Block affected admission or drain; never undercount ownership             |
| Fatal owner inconsistency                                                | Enter `Draining`, preserve strongest evidence, clean trees, exit nonzero  |
| Shutdown cleanup remains incomplete                                      | Exit nonzero; never report normal completion                              |

## Compatibility

Configuration names, bootstrap-channel contract, EIP version, descriptor, and package version are independent axes. Unknown authority-bearing config fails closed. Adding an optional non-authority configuration field is compatible; changing a field's authority, transport role, credential location, local-ready timing, generation lifetime, or cleanup guarantee requires explicit compatibility review.

Provider configuration changes restart envd and create a new generation. Live EIP requests never migrate mount, isolation, carrier, or quota policy.

## Invariants

01. Envd becomes locally ready only after trusted configuration, fresh generation-private runtime state, bounded owners, and required platform isolation probe succeed.
02. Envd supports trusted stdio or outbound reverse WebSocket and never binds an inbound EIP/HTTP/WebSocket/health listener.
03. Reverse-WebSocket attachment uses a protected short-lived credential source with refresh or explicit generation-fatal expiry behavior; credentials never enter URLs, EIP, argv, child environments, or observability.
04. Authority-bearing configuration is immutable for one generation; sessions observe only configured mounts and exact available methods.
05. Every request, response, queue, operation, transfer, staging, process, spool, and shutdown resource is finitely bounded.
06. One operation ledger owns running admission and retained terminal replay/receipt evidence for effectful methods; response-waiter loss cannot erase accepted mutation evidence.
07. Command output capacity is reserved before payload release, and valid process/output records are reclaimed only explicitly or at generation end.
08. Required isolation probes Linux, macOS, or Windows before carrier admission and never selects disabled after failure.
09. Local readiness and initialized-carrier readiness remain distinct; reconnect does not change generation or erase generation-owned resources.
10. Each start exclusively locks its dedicated runtime parent, proves removal of every validated crash-left generation tree, then creates fresh command-home, command-temp, spool, control, and probe state; stale spool bytes are never left outside current capacity accounting while service starts.
11. Shutdown stops admission before cleanup, terminates every owned command tree, removes volatile generation state, and reports uncertainty rather than false success.

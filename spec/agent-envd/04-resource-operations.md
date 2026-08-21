# Resource Operations

## Design Position

`agent-envd` exposes semantic, mount-scoped filesystem operations and bounded observation of local listening ports. It resolves native paths and races inside the Environment boundary rather than exposing host absolute paths or asking the EIP client to emulate filesystem behavior through low-level syscalls.

Trusted daemon configuration defines every native mount root and its maximum access. EIP requests select logical mount IDs and relative paths within those roots; they cannot add a host path, remount a resource, or widen read-only policy.

## Boundaries

| Concern                                                                             | Owner                                      | Relationship                                |
| ----------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------- |
| Native mount roots, writable ceilings, protected roots, and port-observation policy | Operator or provider adapter               | Trusted immutable daemon configuration      |
| Harness virtual `/workspace` and `/environment/{alias}` routing                     | Harness                                    | Resolves to one binding before EIP dispatch |
| Logical mount paths and file, search, and port methods                              | This document                              | Stable EIP resource contract                |
| Native path canonicalization, symlink containment, compare-and-swap, and receipts   | `agent-envd`                               | Authoritative provider enforcement          |
| Provider ingress, public URL, tunnel, or container port publishing                  | Provider adapter                           | Outside EIP port observation                |
| Per-result bytes, references, cursors, and aggregate quotas                         | [Output Retention](06-output-retention.md) | Applies while resource output is produced   |

Filesystem selectors and port numbers are not authority. Every method also crosses authentication, capability checks, current generation, and configured ceilings.

## Mount Model

Trusted daemon configuration uses this conceptual, non-EIP shape:

```python
class TrustedMountConfig(BaseModel):
    mount_id: str
    native_root: str
    writable: bool
    allow_command_execution: bool = True
    max_file_bytes: int
    allowed_operations: frozenset[str]
```

`native_root` is operator-only secret-adjacent configuration: envd canonicalizes and identity-checks it but never returns it in EIP. `allowed_operations` is a subset of the file-read, file-write, search, command-cwd, and executable-source families supported by the daemon. `writable=false` is an absolute ceiling for file methods and required-isolation command grants.

The following objects are serialized EIP JSON:

```python
class EIPPath(BaseModel):
    mount_id: str
    path: str


class MountDescriptor(BaseModel):
    mount_id: str
    logical_root: str
    writable: bool
    case_sensitive: bool | None
    supports_atomic_replace: bool
    supports_file_revision: bool
    max_file_bytes: int


class FileRevision(BaseModel):
    value: str
```

`mount_id` is a bounded stable logical ID within one Environment generation. `logical_root` is a display-only EIP path such as `/`; it is not a host path. `path` is absolute within that logical mount, begins with `/`, uses `/` separators on the wire, contains no NUL, and is validated lexically before native resolution. `/` is the mount root; otherwise empty interior segments, a trailing separator, `.`, and `..` are rejected instead of normalized into another request.

A trusted mount configuration binds the logical ID to one canonical native directory, read-only or read-write ceiling, protected-path checks, allowed operation families, file-size bounds, and platform semantics. The descriptor reports observed behavior but cannot widen the configuration.

Native mount roots are non-overlapping after canonicalization unless they are exact aliases with identical policy intentionally represented as one mount. Ancestor/descendant roots with competing policies are rejected at startup. This avoids backend-specific precedence and accidental widening.

## Canonicalization and Symlinks

For every operation, envd:

1. selects the trusted mount by exact `mount_id`;
2. validates the lexical wire path and operation-specific size bounds;
3. resolves components relative to an already opened or identity-checked mount root;
4. checks every traversed symlink target remains inside the same authorized root and outside protected paths;
5. applies read/write and operation policy to the resolved resource;
6. uses descriptor-relative, no-follow, file-identity, or equivalent race-resistant native operations where available;
7. revalidates mutation preconditions at the commit boundary.

A symlink can narrow convenience but cannot expand authority. A link inside a writable workspace that targets daemon state, another mount, the host home, or any unexposed path remains inaccessible. The daemon does not return a native canonical path to the client.

`file.stat` can inspect the link itself or a contained target through an explicit `follow_symlinks` flag. Read, list, search, copy-source, and patch operations follow contained symlinks by default. Write replacement does not replace an out-of-root target. `file.remove` removes the selected directory entry itself and never recursively follows a symlink. Recursive traversal tracks visited directory identities and has finite depth and entry ceilings.

Canonicalization failure is pre-dispatch for the requested filesystem mutation. A root or component identity changed during setup returns a conflict or denial; envd never retries against the replacement path silently.

## Common File Types

```python
type FileKind = Literal[
    "file", "directory", "symlink", "other"
]


class FileInfo(BaseModel):
    path: EIPPath
    kind: FileKind
    size_bytes: int | None
    modified_at: datetime | None
    executable: bool | None
    revision: FileRevision | None
```

`FileRevision` is an opaque provider comparison value scoped to Environment generation and resource identity. It is not content, authority, or guaranteed stable across replacement. When a provider cannot produce a safe revision, the field is `null` and compare-and-swap requiring it is unsupported.

Timestamps and executable bits are observations with platform-specific precision. EIP does not expose owner IDs, ACL internals, native inode numbers, extended attributes, device nodes, sockets, or arbitrary platform metadata through the base file contract.

## Read Operations

### `file.stat`

```python
class FileStatParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    follow_symlinks: bool = True


class FileStatResult(BaseModel):
    info: FileInfo
```

A missing path returns `not_found_or_denied`. Following a symlink that leaves the mount returns `denied` without disclosing the target.

### `file.read`

```python
class FileReadParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    offset: int = 0
    length: int | None = None
    expected_revision: FileRevision | None = None
    output_policy: OutputPolicy | None = None


class FileReadResult(BaseModel):
    info: FileInfo
    range_start: int
    range_end: int
    output: OutputCapture
```

The daemon opens and verifies the file before reporting `info`; `expected_revision` prevents reading a different replacement when supplied. `offset` and `length` select a byte range, not text characters. The selected range and response remain subject to effective `OutputPolicy`; a client reads additional ranges or a retained reference rather than requesting an unbounded body.

`range_start` and `range_end` identify the contiguous half-open file interval captured by this result, inline or through its reference. `range_start` equals the requested `offset`. Under truncation, envd captures a contiguous prefix, sets `range_end` to the first unread file offset, and does not substitute a tail preview. When `info.revision` is present, the next request continues at `range_end` with that value as `expected_revision`; a concurrent replacement then returns `conflict` rather than combining bytes from two versions. This range contract lets clients stream arbitrarily large binary files and images through bounded responses without guessing encoded sizes.

A mount that cannot produce a safe revision still supports bounded range reads, but separate requests are separate observations and can see different file versions. The client selects explicit offsets and lengths and accepts that lack of snapshot consistency, or uses a provider that advertises revision support when one-version streaming is required. Envd never fabricates a revision or claims cross-request consistency it cannot prove.

`output.producer_complete=true` and `output.content_complete=true` mean the requested range was captured completely from the file observation used for that call. They do not mean the entire file was requested.

### `file.list`

```python
class FileListParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    recursive: bool = False
    max_depth: int = 1
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileListEntry(BaseModel):
    relative_path: str
    info: FileInfo


class FileListResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    output: StructuredOutputDisposition
```

Entries are returned in a documented stable bytewise path order for one captured request shape. A cursor binds mount, canonical root, recursive flag, depth, generation, ordering, and authorization. Directory mutation between pages can produce a typed invalidated cursor or a provider snapshot when advertised; EIP never presents an unstable traversal as a complete snapshot.

Directory entry names are bounded. A native name that cannot be represented safely in EIP UTF-8 causes an explicit `unsupported` failure before the affected page is returned; it is never silently lossy-decoded, skipped, or represented as another path.

### `file.find`

`file.find` searches path names only. It never opens regular-file content.

```python
class FileFindParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    pattern: str
    mode: Literal["glob", "regex"]
    kind: FileKind | None = None
    max_depth: int
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileFindResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    output: StructuredOutputDisposition
```

The pattern matches each descendant's `/`-separated path relative to `root`, never a native path; `root` itself is not a result. Glob syntax is the EIP subset `*`, `?`, `**`, and bracket character classes: `*`, `?`, and classes never match `/`, while `**` matches zero or more complete path segments. Regex mode uses UTF-8 RE2-style syntax without look-around or backreferences and matches the complete relative path. Results use the same stable bytewise relative-path order and cursor invalidation rules as `file.list`.

### `file.search`

`file.search` searches regular-file content. Path selection is separate from content matching: `include` and `exclude` are EIP path globs relative to `root`, while `query` is matched only against decoded file content.

```python
class FileSearchParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    query: str
    mode: Literal["literal", "regex"]
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    case_sensitive: bool = True
    max_depth: int
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileSearchMatch(BaseModel):
    path: EIPPath
    line_number: int
    byte_offset: int
    preview: str


class FileSearchResult(BaseModel):
    matches: tuple[FileSearchMatch, ...]
    output: StructuredOutputDisposition
```

Search reads UTF-8 regular files line by line. A file containing invalid UTF-8 or NUL is skipped deterministically and never lossily decoded. `include=()` selects every relative path; otherwise a file must match at least one include glob, and any matching exclude glob wins. Both use the `file.find` glob dialect.

`line_number` is one-based, `byte_offset` is the zero-based raw-file offset of the match, and `preview` is one bounded containing line with line terminators removed. Results are ordered by relative path, then byte offset. Literal mode emits every non-overlapping occurrence of the exact query text. Regex mode uses the same RE2-style syntax as `file.find`, is applied independently to each line, and cannot span line boundaries; a zero-width match advances by one Unicode scalar before another match is considered. `case_sensitive=false` uses Unicode simple case folding and is explicit rather than inferred from the filesystem.

Query length, regex complexity, glob count, traversal depth, files visited, bytes scanned, match count, preview bytes, and total duration are finite. Find and search implementations use bounded streaming and cancellation. A policy cutoff returns an incomplete `StructuredOutputDisposition` and continuation cursor when the traversal can continue safely; a deadline or cancellation uses the typed EIP error and can include only bounded partial-output metadata. Absence of further results is claimed only when traversal completed.

Neither method follows a symlink outside the selected mount or reads special files. Content previews are untrusted file content and follow the Harness content and redaction policy after provider-side size enforcement. `file.find` and `file.search` have independent capabilities; omitting either capability is valid, but advertising one requires its complete semantics.

## Mutation Operations

All mutations return an `OperationReceipt` and the resulting resource revision when known. A method validates its entire semantic request before native commit. Idempotency support is method- and mode-specific and follows [EIP Protocol](02-eip-protocol.md#common-operation-context).

### `file.write`

```python
type WriteMode = Literal[
    "create", "replace", "upsert", "append", "write_at"
]


class FileWriteParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    mode: WriteMode
    data: EncodedBytes
    offset: int | None = None
    expected_revision: FileRevision | None = None
    create_parents: bool = False
    executable: bool | None = None


class FileWriteResult(BaseModel):
    info: FileInfo
    bytes_written: int
    receipt: OperationReceipt
```

`data` is bounded by request and file limits before allocation. Larger transfers use repeated bounded `write_at` calls with revision preconditions. The base protocol never accepts an unbounded request stream or claims that a multi-request upload is atomic.

- `create` fails if the path exists.
- `replace` requires an existing regular file and replaces it atomically when the descriptor advertises support.
- `upsert` creates or replaces.
- `append` appends one bounded byte sequence and requires an idempotency key for replay-safe mutation.
- `write_at` requires a non-negative offset and explicit expected revision when provider policy requires concurrent-write safety.

For create, replace, and upsert, envd writes to a private file in the same authorized filesystem, validates size and metadata, fsyncs according to configured durability policy, and commits by atomic rename when supported. A successful response never exposes a partially written destination. Provider durability after OS acknowledgement is not Host durable Agent completion.

### `file.mkdir`

```python
class FileMkdirParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    parents: bool = False
    exist_ok: bool = False


class FileMkdirResult(BaseModel):
    info: FileInfo
    created_directories: int
    receipt: OperationReceipt
```

Creates one directory or a bounded parent chain under the selected mount. Existing-path behavior is explicit through `exist_ok`. It never treats a symlink as a directory for creation.

### `file.patch`

```python
class FilePatchParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    patch_format: Literal["unified_diff"]
    patch: str
    expected_revision: FileRevision


class FilePatchResult(BaseModel):
    info: FileInfo
    hunks_applied: int
    receipt: OperationReceipt
```

The patch, target file, line lengths, hunk count, and resulting bytes are bounded. Envd validates the complete unified diff against exactly `expected_revision`, applies every hunk to a bounded private replacement, and atomically commits all or none. Fuzz matching that could modify an unintended location is not part of the base contract.

### `file.copy`

```python
class FileCopyParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    expected_source_revision: FileRevision | None = None
    expected_destination_revision: FileRevision | None = None
    replace: bool = False
    require_atomic_destination: bool = False


class FileCopyResult(BaseModel):
    destination: FileInfo
    bytes_copied: int
    atomic_destination: bool
    receipt: OperationReceipt
```

Copy reads and writes in bounded chunks and separately checks source-read and destination-write authority. Source revision can be required. When `require_atomic_destination=true`, lack of a same-filesystem atomic destination commit returns `unsupported` before dispatch. Otherwise the operation can use a non-atomic destination and reports `atomic_destination=false` in its result. The destination never aliases the source through a symlink escape.

Cross-Environment copy is not an EIP method. The Harness performs an explicit bounded source read and destination write through two independently authorized bindings. Within one envd instance, copying between configured mounts remains one method only when both mounts permit it and no protected or isolation boundary is crossed.

### `file.move`

```python
class FileMoveParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    expected_source_revision: FileRevision | None = None
    expected_destination_revision: FileRevision | None = None
    replace: bool = False


class FileMoveResult(BaseModel):
    destination: FileInfo
    receipt: OperationReceipt
```

Move is available only when envd can provide one atomic rename within the same configured mount and filesystem. Cross-mount or copy-then-delete move returns `unsupported` rather than presenting two mutations as atomic. Destination replacement behavior and expected source/destination revisions are explicit.

### `file.remove`

```python
class FileRemoveParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    expected_kind: FileKind
    expected_revision: FileRevision | None = None
    recursive: bool = False
    max_entries: int = 1


class FileRemoveResult(BaseModel):
    removed_entries: int
    receipt: OperationReceipt
```

Removal requires the expected kind and optional expected revision. Directory removal is non-recursive by default. Recursive removal has finite depth and entry limits, never follows symlinks, and is rejected when the implementation cannot preserve protected-path and race guarantees. A recursive partial failure returns a receipt describing known progress; it never claims rollback.

## Local Port Observation

EIP port methods observe local TCP listener readiness inside the selected Environment. They do not allocate a provider ingress route, publish a URL, modify a firewall, create a tunnel, or grant network authority.

```python
type PortAddress = Literal["loopback", "any"]
type PortStatus = Literal[
    "listening", "not_listening", "unknown"
]


class PortTarget(BaseModel):
    protocol: Literal["tcp"]
    address: PortAddress
    port: int


class PortObservation(BaseModel):
    target: PortTarget
    status: PortStatus
    managed_process: ProcessHandle | None
    observed_at: datetime


class PortInspectParams(BaseModel):
    context: EIPCallContext
    target: PortTarget


class PortInspectResult(BaseModel):
    observation: PortObservation


class PortWaitParams(BaseModel):
    context: EIPCallContext
    target: PortTarget
    desired_status: Literal["listening", "not_listening"]


class PortWaitResult(BaseModel):
    observation: PortObservation
```

`port.inspect` returns one observation. `port.wait` uses the `EIPCallContext.deadline` as its finite wait boundary and returns when the desired status is observed or that deadline expires. The base contract follows the Linux/POSIX TCP port domain: `port` is an integer in `1..65535`; port `0` is valid for listener allocation but is never an observable listening target. Availability of `port.observe` and any narrower current policy determine whether the call is admitted; EIP does not define a configurable default port-range grant. Arbitrary remote hosts, UDP scanning, raw sockets, packet capture, and host-network enumeration are not part of this capability.

When the platform can safely attribute a listener to an envd-managed process in the current daemon generation, `managed_process` can be returned. An unmanaged listener is reported only as policy permits and never reveals a PID or identity. `unknown` is used when namespace, platform, or permission prevents trustworthy observation.

A command in isolated Linux `deny` networking has its own empty network namespace and cannot expose an IP listener to envd or the provider. A command using `host` network or an explicit outer sandbox network can be observed only from the network boundary where envd runs. The descriptor reports capability honestly.

Provider adapters own the mapping from a successfully observed local port to a public, tunneled, or container-exposed endpoint. EIP never treats listening status as proof that an external route exists or is authorized.

## Resource Lifetime

Native files and directories are provider Environment state and can remain after envd exits. EIP does not serialize a filesystem snapshot or daemon registry. Process handles, operation records, receipts, output references, traversal cursors, and private spool data exist only in the current daemon generation. A fresh authenticated protocol session in that generation can continue using them; a daemon restart cannot.

## Failure Semantics

| Failure                                        | Outcome                                                             | Side-effect meaning                                                 |
| ---------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Invalid logical path, mount, query, or port    | `invalid_params` or `denied`                                        | Pre-dispatch                                                        |
| Symlink or canonical target escapes policy     | `denied`                                                            | Pre-dispatch for requested mutation                                 |
| File revision or resource precondition changed | `conflict`                                                          | No requested commit when detected before commit                     |
| Output or traversal bound reached              | Explicit incomplete disposition, quota error, or output-limit error | Read/search may have observed content; no hidden completeness claim |
| Atomic replacement unsupported                 | `unsupported` before dispatch when atomicity was required           | No destination mutation                                             |
| Transport lost during mutation                 | Receipt or `unknown_outcome` according to commit evidence           | Reconcile before retry                                              |
| Recursive removal partially completes          | Failed receipt with known progress                                  | No rollback claim                                                   |
| Port cannot be observed safely                 | `status="unknown"` or `unsupported`                                 | No listener mutation                                                |
| Generation-local selector expired or stale     | `retention_gap`, `invalid_handle`, or `stale_generation`            | No fabricated continuation                                          |

## Compatibility

File method semantics are capability-gated independently from native platform. New metadata fields can be additive, but changing path normalization, symlink behavior, write-mode defaults, atomicity, revision scope, search dialect, or traversal ordering requires an incompatible protocol revision.

Providers can expose narrower limits and omit unsupported methods. A client never infers support from operating system, Docker/E2B labels, or daemon package version. Common conformance tests use symlink escapes, concurrent replacement, output bounds, mutation preconditions, receipt ambiguity, path-find/content-search distinctions, and cursor invalidation fixtures.

## Invariants

01. Every filesystem operand selects one trusted logical mount and never accepts a native root from request data; a multi-path operation authorizes each operand independently.
02. Lexical validation and native canonicalization both apply; symlinks cannot expand authority across mounts, protected paths, or host roots.
03. Read-only mount policy constrains both file methods and command filesystem grants.
04. Every traversal, query, patch, request body, file size, result, and duration has a finite bound.
05. A complete file or search result refers to one verified request shape and revision/snapshot semantics; mutation races never create false completeness.
06. Atomic mutation is claimed only when the native commit primitive provides it; cross-mount move never masquerades as atomic.
07. Every mutating method returns bounded side-effect evidence and preserves unknown outcome after ambiguous transport loss.
08. Port methods observe only policy-authorized local TCP targets in `1..65535` and never create external exposure or scan remote hosts.
09. `file.find` matches relative path names and never reads file content; `file.search` matches UTF-8 regular-file content and uses path globs only for file selection.
10. Native files can outlive envd, while process, operation, receipt, output, cursor, and spool records never outlive their daemon generation.
11. Provider lifecycle state, transport state, and Host durable execution state never enter an EIP state export because EIP defines no state export or restore method.

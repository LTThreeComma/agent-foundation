"""What callers submit to threads and read back about sessions, threads, input and runs."""

import json
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.connections.headers import normalize_headers

type RunStatus = Literal["accepted", "running", "waiting", "completed", "failed", "cancelled"]
type Trigger = Literal["input", "queued", "resume", "child_result", "spawned"]
type Lineage = Literal["root", "continue", "fork"]
type EntryStatus = Literal["pending", "assigned", "consumed", "failed", "withdrawn"]
type Delivery = Literal["steer", "next_run"]
type PendingKind = Literal["approval", "client_tool", "user_input"]
type WaitReason = Literal["approval", "client_tool", "user_input", "multiple"]

MAX_JSON_DEPTH = 32
MAX_RESUME_BYTES = 262144


def canonical_json(value: object) -> bytes:
    """The one serialization used for sizes and digests of stored payloads and requests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Failure(_Frozen):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(max_length=4096)


class TextPart(_Frozen):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=65536)


class AssetPart(_Frozen):
    type: Literal["asset"]
    asset_id: ObjectId


class UrlPart(_Frozen):
    type: Literal["url"]
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def plain_http(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("URL input requires a plain HTTP(S) URL")
        return value


class JsonPart(_Frozen):
    type: Literal["json"]
    value: JsonValue

    @field_validator("value")
    @classmethod
    def bounded(cls, value: JsonValue) -> JsonValue:
        def check(item: JsonValue, depth: int) -> None:
            if depth > MAX_JSON_DEPTH:
                raise ValueError("Structured input nesting exceeds its limit")
            children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            for child in children:
                check(child, depth + 1)

        check(value, 0)
        return value


type Part = Annotated[TextPart | AssetPart | UrlPart | JsonPart, Field(discriminator="type")]


class MessagePayload(_Frozen):
    content: tuple[Part, ...] = Field(min_length=1, max_length=32)

    def text(self) -> str:
        return "\n".join(part.text for part in self.content if isinstance(part, TextPart))


class UsageLimit(_Frozen):
    requests: int = Field(ge=1, le=10000)


class RunOptions(_Frozen):
    """What a message may choose for the run it starts. A steer joins a run only with equal options."""

    labels: Labels = Field(default_factory=dict)
    max_usage: UsageLimit | None = None


type McpHeaders = Annotated[dict[ObjectId, dict[str, str]], Field(max_length=32)]


def normalize_mcp_headers(value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    normalized = {connection: normalize_headers(headers) for connection, headers in value.items()}
    if sum(len(name) + len(item) for headers in normalized.values() for name, item in headers.items()) > 16384:
        raise ValueError("Caller headers exceed their byte limit")
    return normalized


class Message(_Frozen):
    kind: Literal["message"] = "message"
    delivery: Delivery = "steer"
    payload: MessagePayload
    agent_id: ObjectId
    agent_revision_id: ObjectId | None = None
    options: RunOptions = Field(default_factory=RunOptions)


class NewThread(Message):
    session_id: ObjectId | None = None
    mcp_headers: McpHeaders = Field(default_factory=dict)

    @field_validator("mcp_headers")
    @classmethod
    def normalized(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return normalize_mcp_headers(value)


class Fork(Message):
    fresh_environments: bool = False


class ThreadUpdate(_Frozen):
    labels: Labels | None = None
    mcp_headers: McpHeaders | None = None

    @field_validator("mcp_headers")
    @classmethod
    def normalized(cls, value: dict[str, dict[str, str]] | None) -> dict[str, dict[str, str]] | None:
        return None if value is None else normalize_mcp_headers(value)


class EntryUpdate(_Frozen):
    """Pending entries only; the original request digest never changes."""

    delivery: Delivery | None = None
    payload: MessagePayload | None = None
    agent_revision_id: ObjectId | None = None
    options: RunOptions | None = None


class InboxOrder(_Frozen):
    entry_ids: tuple[ObjectId, ...] = Field(min_length=1, max_length=512)

    @field_validator("entry_ids")
    @classmethod
    def unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Entry IDs must be unique")
        return value


class Approve(_Frozen):
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["approve"]


class Reject(_Frozen):
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["reject"]
    reason: str | None = Field(default=None, max_length=4096)


class Complete(_Frozen):
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["complete"]
    result: JsonValue


class NoResponse(_Frozen):
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["no_response"] = "no_response"


type Answer = Annotated[Approve | Reject | Complete, Field(discriminator="action")]
type NormalizedAnswer = Annotated[Approve | Reject | Complete | NoResponse, Field(discriminator="action")]


class ResumeRequest(_Frozen):
    answers: tuple[Answer, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def bounded(self) -> "ResumeRequest":
        if len({answer.tool_call_id for answer in self.answers}) != len(self.answers):
            raise ValueError("Answer tool call IDs must be unique")
        if len(canonical_json(self.model_dump(mode="json"))) > MAX_RESUME_BYTES:
            raise ValueError("Resume request exceeds its byte limit")
        return self


class Resume(_Frozen):
    """The normalized batch stored on the successor: one answer per pending call of the exact wait."""

    answers: tuple[NormalizedAnswer, ...] = Field(max_length=128)


class PendingItem(_Frozen):
    tool_call_id: str
    kind: PendingKind
    tool_name: str
    arguments: dict[str, JsonValue]
    presentation: dict[str, JsonValue] | None = None


class Pending(_Frozen):
    """Public projection of the exact sealed pending set; the native requests live in the state object."""

    items: tuple[PendingItem, ...] = Field(min_length=1, max_length=128)

    @property
    def reason(self) -> WaitReason:
        kinds: set[WaitReason] = {item.kind for item in self.items}
        return kinds.pop() if len(kinds) == 1 else "multiple"

    @property
    def question_only(self) -> bool:
        return all(item.kind == "user_input" for item in self.items)


class EnvironmentMount(_Frozen):
    name: str
    environment_id: str
    working_directory: str | None = None


class SessionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    labels: dict[str, str]
    created_by_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class ThreadView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    session_id: str
    origin: Literal["new", "fork", "child"]
    origin_thread_id: str | None
    origin_run_id: str | None
    origin_tool_call_id: str | None
    current_run_id: str | None
    head_run_id: str | None
    last_run_id: str | None
    mcp_headers: dict[str, dict[str, str]]
    labels: dict[str, str]
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class EntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    thread_id: str
    kind: Literal["message", "child_result"]
    delivery: Delivery
    position: int
    status: EntryStatus
    principal_id: str
    payload: dict[str, JsonValue]
    agent_id: str | None
    agent_revision_id: str | None
    options: dict[str, JsonValue]
    child_run_id: str | None
    origin_run_id: str | None
    assigned_run_id: str | None
    incorporated_checkpoint_seq: int | None
    failure: Failure | None
    created_at: datetime
    finished_at: datetime | None


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    session_id: str
    thread_id: str
    agent_id: str
    agent_revision_id: str
    revision_selection: Literal["pinned", "default", "inherited"]
    principal_id: str
    status: RunStatus
    trigger: Trigger
    lineage: Lineage
    parent_run_id: str | None
    source_entry_id: str | None
    resume: Resume | None
    resumed_by_id: str | None
    wait_reason: WaitReason | None
    pending: Pending | None
    environment_mounts: list[EnvironmentMount]
    current_attempt_id: str | None
    attempts: int
    max_attempts: int
    output: JsonValue | None
    failure: Failure | None
    usage_at_seal: dict[str, JsonValue] | None
    labels: dict[str, str]
    cancel_requested_at: datetime | None
    version: int
    created_at: datetime
    started_at: datetime | None
    sealed_at: datetime | None
    updated_at: datetime


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    number: int
    status: Literal["leased", "running", "succeeded", "yielded", "failed", "cancelled"]
    start_reason: Literal["initial", "recovery", "handoff"]
    replaces_attempt_id: str | None
    worker_build: str
    harness_run_id: str | None
    yield_reason: str | None
    failure: Failure | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class Submitted(BaseModel):
    """A submission receipt: the entry and, when the thread was idle, the run it started."""

    thread: ThreadView
    entry: EntryView
    run: RunView | None

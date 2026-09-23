"""Public inputs and detached execution selections; no live credentials in state."""

import json
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from a13n_harness import HarnessState
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from a13n_service.infra.ids import ObjectId
from a13n_service.resources.agents.schemas import AgentConfig, Label
from a13n_service.resources.connections.headers import normalize_headers
from a13n_service.runs.feedback import FeedbackPayload, FeedbackSubmission
from a13n_service.runs.waiting import PendingItem, Waiting, WaitReason
from a13n_service.tenancy.authorize import ExecutionAuthority

type RunStatus = Literal["accepted", "running", "waiting", "completed", "failed", "cancelled"]
type RunTrigger = Literal["input", "queued", "feedback", "child_result", "spawned"]


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=65536)


class AssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["asset"]
    asset_id: ObjectId

    @field_validator("asset_id")
    @classmethod
    def asset_kind(cls, value: str) -> str:
        if not value.startswith("ast_"):
            raise ValueError("Asset input requires an Asset ID")
        return value


class UrlInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["url"]
    url: str = Field(min_length=8, max_length=2048)

    @field_validator("url")
    @classmethod
    def http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("URL input requires a plain HTTP(S) URL")
        return value


class EnvironmentPathInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["environment_path"]
    mount: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)


class JsonInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["json"]
    value: JsonValue

    @field_validator("value")
    @classmethod
    def bounded_json(cls, value: JsonValue) -> JsonValue:
        def depth(item: JsonValue, level: int) -> None:
            if level > 32:
                raise ValueError("Structured input nesting exceeds its limit")
            if isinstance(item, dict):
                for nested in item.values():
                    depth(nested, level + 1)
            elif isinstance(item, list):
                for nested in item:
                    depth(nested, level + 1)

        depth(value, 0)
        if len(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()) > 65536:
            raise ValueError("Structured input exceeds its byte limit")
        return value


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content: tuple[
        Annotated[TextInput | AssetInput | UrlInput | EnvironmentPathInput | JsonInput, Field(discriminator="type")],
        ...,
    ] = Field(min_length=1, max_length=32)


class UsageLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requests: int = Field(ge=1, le=1000)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    labels: dict[Label, Label] = Field(default_factory=dict, max_length=32)
    max_usage: UsageLimit | None = None
    mcp_headers: dict[ObjectId, dict[str, str]] = Field(default_factory=dict, max_length=32)

    @field_validator("mcp_headers")
    @classmethod
    def normalized_headers(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        normalized = {connection: normalize_headers(headers) for connection, headers in value.items()}
        if sum(len(name) + len(item) for headers in normalized.values() for name, item in headers.items()) > 16384:
            raise ValueError("Caller context exceeds its byte limit")
        return normalized


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["message"]
    delivery: Literal["steer", "next_run"] = "steer"
    payload: MessagePayload
    agent_id: ObjectId
    agent_revision_id: ObjectId | None = None
    options: RunOptions = Field(default_factory=RunOptions)


type InboxSubmission = Annotated[Submission | FeedbackSubmission, Field(discriminator="kind")]


class NewThread(Submission):
    session_id: ObjectId | None = None


class EntryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    thread_id: str
    kind: str
    delivery: str
    position: int
    status: Literal["pending", "assigned", "consumed", "failed", "withdrawn"]
    assigned_run_id: str | None
    incorporated_checkpoint_seq: int | None
    failure: dict | None
    payload: MessagePayload | FeedbackPayload | None
    waiting_run_id: str | None = None


class InboxPage(BaseModel):
    items: list[EntryView]
    next_cursor: str | None


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    thread_id: str
    agent_id: str
    agent_revision_id: str
    status: RunStatus
    trigger: RunTrigger
    wait_reason: WaitReason | None = None
    pending: tuple[PendingItem, ...] | None = None
    current_attempt_id: str | None
    parent_run_id: str | None
    source_entry_id: str
    output: dict | None
    failure: dict | None
    labels: dict[str, str]
    version: int
    created_at: datetime
    sealed_at: datetime | None
    cancel_requested_at: datetime | None

    @field_validator("pending", mode="before")
    @classmethod
    def project_pending(cls, value):
        return Waiting.model_validate(value).items() if isinstance(value, dict) else value


class Submitted(BaseModel):
    thread_id: str
    session_id: str
    entry: EntryView
    run: RunView | None
    replayed: bool


class AgentSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_id: str
    revision_id: str
    digest: str
    config: AgentConfig


class AttemptClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str
    attempt_id: str
    thread_id: str
    organization_id: str
    workspace_id: str
    number: int
    worker_id: str
    token: str = Field(repr=False)
    lease_expires_at: datetime


class ExecutionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunView
    agent: AgentSelection
    authority: ExecutionAuthority
    options: RunOptions
    parent_checkpoint: dict | None
    parent_waiting: Waiting | None = None


class DisplayCut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int = Field(ge=0)
    attempt_id: str
    event_sequence: int = Field(ge=0)


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["a13n.service.checkpoint.v1"] = "a13n.service.checkpoint.v1"
    organization_id: str
    run_id: str
    attempt_id: str
    attempt_number: int = Field(ge=1)
    sequence: int = Field(ge=1)
    agent_revision_id: str
    agent_digest: str
    options_digest: str
    state: HarnessState
    receipts: tuple[str, ...] = Field(max_length=10000)
    display_cut: DisplayCut
    candidate: Literal["completed", "waiting"] | None = None
    output: str | None = None
    waiting: Waiting | None = None

    @model_validator(mode="after")
    def waiting_candidate(self):
        if (self.candidate == "waiting") != (self.waiting is not None):
            raise ValueError("Waiting candidates require their exact pending batch")
        if self.candidate == "waiting" and self.output is not None:
            raise ValueError("Waiting candidates have no completed output")
        return self


class SnapshotRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    digest: str
    size: int = Field(ge=0)
    content_type: Literal["application/json"] = "application/json"
    format: str
    sequence: int = Field(ge=1)
    attempt_id: str
    version: str

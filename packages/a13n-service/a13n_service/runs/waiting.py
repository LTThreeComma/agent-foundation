"""Exact native waiting batches and their public, non-authoritative projection."""

from __future__ import annotations

from typing import Literal

from a13n_harness.tools.deferred import validate_deferred_requests
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from pydantic_ai.tools import DeferredToolRequests

type PendingKind = Literal["approval", "client_tool", "user_input"]
type WaitReason = Literal["approval", "client_tool", "user_input", "multiple"]


class ToolTarget(BaseModel):
    """The concrete Connection/account against which an approval was requested."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    connection_id: str
    version: int
    authorization_id: str | None = None
    authorization_generation: int | None = None


class PendingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: str
    kind: PendingKind
    tool_name: str
    arguments: dict[str, JsonValue]


class Waiting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requests: DeferredToolRequests
    targets: dict[str, ToolTarget] = Field(default_factory=dict, max_length=128)

    @model_validator(mode="after")
    def complete_batch(self) -> Waiting:
        _, approval_ids = validate_deferred_requests(self.requests)
        if self.targets.keys() != approval_ids:
            raise ValueError("Every pending approval requires its exact tool target")
        return self

    def items(self) -> tuple[PendingItem, ...]:
        return tuple(
            PendingItem(
                tool_call_id=call.tool_call_id,
                kind="approval"
                if approval
                else "user_input"
                if call.tool_name == "ask_user_question"
                else "client_tool",
                tool_name=call.tool_name,
                arguments=call.args_as_dict(),
            )
            for approval, calls in ((False, self.requests.calls), (True, self.requests.approvals))
            for call in calls
        )

    @property
    def reason(self) -> WaitReason:
        kinds: set[PendingKind] = {item.kind for item in self.items()}
        return next(iter(kinds)) if len(kinds) == 1 else "multiple"

    @property
    def question_only(self) -> bool:
        return all(item.kind == "user_input" for item in self.items())

"""Retained supplied deferred facts; approvals never become portable replay grants."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator
from pydantic_ai import ToolDenied, ToolFailed, ToolReturn
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, ToolReturnPart
from pydantic_ai.tools import DeferredToolRequests

from a13n_harness._json import dump_json_bytes
from a13n_harness.errors import RunError, StateError
from a13n_harness.state import AgentContextState, AgentContextStateSnapshot
from a13n_harness.tools.deferred import DeferredToolResume, validate_deferred_requests

STATE_ID = "a13n.tool-execution-boundary.deferred-results"
STATE_VERSION = "1"
MAX_STATE_BYTES = 1024 * 1024


class JsonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["json"] = "json"
    value: JsonValue


class ReturnResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["return"] = "return"
    value: ToolReturn[JsonValue]


class FailedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["failed", "denied"]
    message: str


type SuppliedResult = Annotated[JsonResult | ReturnResult | FailedResult, Field(discriminator="kind")]


def encode(value: object) -> SuppliedResult:
    if isinstance(value, ToolFailed):
        return FailedResult(kind="failed", message=value.message)
    if isinstance(value, ToolDenied):
        return FailedResult(kind="denied", message=value.message)
    if isinstance(value, ToolReturn):
        return ReturnResult(value=TypeAdapter(ToolReturn[JsonValue]).validate_python(value))
    return JsonResult(value=TypeAdapter(JsonValue).validate_python(value))


def decode(value: SuppliedResult) -> JsonValue | ToolReturn[JsonValue] | ToolFailed | ToolDenied:
    if isinstance(value, FailedResult):
        return ToolFailed(value.message) if value.kind == "failed" else ToolDenied(value.message)
    return value.value


class DeferredRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requests: DeferredToolRequests
    supplied: dict[str, SuppliedResult]

    @model_validator(mode="after")
    def validate_batch(self) -> DeferredRecord:
        try:
            calls, approvals = validate_deferred_requests(self.requests)
            if not calls <= self.supplied.keys() <= calls | approvals:
                raise ValueError("Retained external results must exactly cover external calls.")
            if any(
                not isinstance(self.supplied[key], FailedResult) or self.supplied[key].kind != "denied"
                for key in self.supplied.keys() & approvals
            ):
                raise ValueError("Only denial facts may be retained for approval calls.")
            encoded = dump_json_bytes(self.model_dump(mode="json"))
        except (ValueError, TypeError, RunError) as exc:
            raise StateError("Deferred continuation is invalid.", code="deferred_state_invalid") from exc
        if len(encoded) > MAX_STATE_BYTES:
            raise StateError("Deferred continuation exceeds its byte limit.", code="deferred_state_too_large")
        return self

    @classmethod
    def capture(cls, resume: DeferredToolResume) -> DeferredRecord:
        supplied = {key: encode(value) for key, value in resume.results.calls.items()}
        for key, decision in resume.results.approvals.items():
            if decision is False or isinstance(decision, ToolDenied):
                supplied[key] = encode(ToolDenied() if decision is False else decision)
        record = cls(requests=resume.requests, supplied=supplied)
        detached = cls.model_validate_json(record.model_dump_json())
        if detached != record:
            raise StateError("Deferred results cannot be durably represented.", code="deferred_state_invalid")
        return detached

    def remaining(self, messages: Sequence[ModelMessage]) -> dict[str, SuppliedResult]:
        """Validate exact correlation and remove only canonically incorporated facts."""
        calls = {call.tool_call_id: call for call in (*self.requests.calls, *self.requests.approvals)}
        if not self.supplied.keys() <= calls.keys():
            raise StateError("Deferred state references an unknown call.", code="deferred_state_invalid")
        response_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if isinstance(messages[index], ModelResponse)),
            None,
        )
        if response_index is None:
            raise StateError("Deferred state has no matching response.", code="deferred_state_invalid")
        response = messages[response_index]
        assert isinstance(response, ModelResponse)
        history = {call.tool_call_id: call for call in response.tool_calls}
        if len(history) != len(response.tool_calls):
            raise StateError("Deferred history has duplicate calls.", code="deferred_state_invalid")
        for key, call in calls.items():
            actual = history.get(key)
            if actual is None or actual.tool_name != call.tool_name or actual.args_as_dict() != call.args_as_dict():
                raise StateError("Deferred state does not match its pending batch.", code="deferred_state_invalid")
        remaining = dict(self.supplied)
        for message in messages[response_index + 1 :]:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ToolReturnPart) or part.tool_call_id not in remaining:
                    continue
                expected = remaining[part.tool_call_id]
                content = (
                    expected.message
                    if isinstance(expected, FailedResult)
                    else (expected.value.return_value if isinstance(expected, ReturnResult) else expected.value)
                )
                outcome = expected.kind if isinstance(expected, FailedResult) else "success"
                if (
                    part.tool_name != calls[part.tool_call_id].tool_name
                    or part.content != content
                    or part.outcome != outcome
                ):
                    raise StateError("Deferred result conflicts with canonical history.", code="deferred_state_invalid")
                if isinstance(expected, ReturnResult) and part.metadata != expected.value.metadata:
                    raise StateError("Deferred result metadata conflicts with history.", code="deferred_state_invalid")
                remaining.pop(part.tool_call_id)
        return remaining

    def incorporated(self, messages: Sequence[ModelMessage]) -> bool:
        if self.remaining(messages):
            return False
        calls = {call.tool_call_id for call in (*self.requests.calls, *self.requests.approvals)}
        response_index = max(index for index, message in enumerate(messages) if isinstance(message, ModelResponse))
        completed = {
            part.tool_call_id
            for message in messages[response_index + 1 :]
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        }
        return calls <= completed


class DeferredState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    batch: DeferredRecord | None = None


def restored(snapshot: AgentContextStateSnapshot) -> DeferredRecord | None:
    entry = snapshot.entries.get(STATE_ID)
    if entry is None:
        return None
    if entry.version != STATE_VERSION:
        raise StateError("Deferred state version is unsupported.", code="deferred_state_invalid")
    if len(dump_json_bytes(entry.data)) > MAX_STATE_BYTES:
        raise StateError("Deferred continuation exceeds its byte limit.", code="deferred_state_too_large")
    return DeferredState.model_validate(entry.data).batch


async def activate(state: AgentContextState, resume: DeferredToolResume, messages: Sequence[ModelMessage]) -> None:
    prior = await state.read(STATE_ID, DeferredState, version=STATE_VERSION)
    if prior is not None and prior.batch is not None and prior.batch.remaining(messages):
        raise StateError("Previous deferred facts have not been incorporated.", code="deferred_state_unincorporated")
    await state.write(STATE_ID, DeferredState(batch=DeferredRecord.capture(resume)), version=STATE_VERSION)


async def reconcile(state: AgentContextState, messages: Sequence[ModelMessage]) -> None:
    current = await state.read(STATE_ID, DeferredState, version=STATE_VERSION)
    if current is not None and current.batch is not None and current.batch.incorporated(messages):
        await state.write(STATE_ID, DeferredState(), version=STATE_VERSION)

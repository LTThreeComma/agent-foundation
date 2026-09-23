"""Bounded complete control answers for one exact sealed waiting run."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from a13n_harness import DeferredToolResume
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from pydantic_ai import ToolDenied, ToolFailed
from pydantic_ai.tools import DeferredToolResults

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import ObjectId
from a13n_service.runs.waiting import Waiting

MAX_FEEDBACK_BYTES = 262144
NO_RESPONSE = "No response was provided for this pending request."


class Approve(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["approve"]


class Reject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["reject"]
    reason: str | None = Field(default=None, max_length=4096)


class Complete(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["complete"]
    result: JsonValue


class NoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_call_id: str = Field(min_length=1, max_length=1024)
    action: Literal["no_response"] = "no_response"


type Answer = Annotated[Approve | Reject | Complete, Field(discriminator="action")]
type NormalizedAnswer = Annotated[Approve | Reject | Complete | NoResponse, Field(discriminator="action")]


class FeedbackSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["feedback"]
    waiting_run_id: ObjectId
    answers: tuple[Answer, ...] = Field(default=(), max_length=128)

    @model_validator(mode="after")
    def bounded(self) -> FeedbackSubmission:
        if len({answer.tool_call_id for answer in self.answers}) != len(self.answers):
            raise ValueError("Feedback call IDs must be unique")
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > MAX_FEEDBACK_BYTES:
            raise ValueError("Feedback exceeds its independent byte limit")
        return self


class FeedbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    answers: tuple[NormalizedAnswer, ...] = Field(min_length=1, max_length=128)

    def resume(self, waiting: Waiting) -> DeferredToolResume:
        results = DeferredToolResults()
        for answer in self.answers:
            if isinstance(answer, Approve):
                results.approvals[answer.tool_call_id] = True
            elif isinstance(answer, Reject):
                results.approvals[answer.tool_call_id] = ToolDenied(answer.reason) if answer.reason else False
            else:
                results.calls[answer.tool_call_id] = (
                    answer.result if isinstance(answer, Complete) else ToolFailed(NO_RESPONSE)
                )
        return DeferredToolResume(waiting.requests, results)


def normalize(waiting: Waiting, submission: FeedbackSubmission) -> FeedbackPayload:
    if waiting.question_only:
        raise ServiceError("invalid_argument", "Questions require an ordinary message")
    supplied = {answer.tool_call_id: answer for answer in submission.answers}
    pending = {item.tool_call_id: item for item in waiting.items()}
    if not supplied.keys() <= pending.keys():
        raise ServiceError("invalid_argument", "Feedback contains an unknown pending call")
    answers: list[NormalizedAnswer] = []
    for call_id, item in pending.items():
        answer = supplied.get(call_id)
        if answer is not None and not (
            (item.kind == "approval" and isinstance(answer, Approve | Reject))
            or (item.kind == "client_tool" and isinstance(answer, Complete))
        ):
            raise ServiceError("invalid_argument", "Feedback answer does not match the pending request kind")
        answers.append(
            answer
            or (
                Reject(tool_call_id=call_id, action="reject")
                if item.kind == "approval"
                else NoResponse(tool_call_id=call_id)
            )
        )
    return FeedbackPayload(answers=tuple(answers))

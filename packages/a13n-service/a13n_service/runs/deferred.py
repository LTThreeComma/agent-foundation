"""The Harness deferred-tool contract: what a suspended run waits for, and the resume that answers it.

The native requests are stored unchanged in the waiting run's checkpoint, because the Harness re-reads their
metadata on resume. The public `Pending` projection is derived from them once, when the run suspends.
"""

from a13n_harness import DeferredToolResume
from a13n_harness.tools.approval import APPROVAL_PRESENTATION_KEY
from a13n_harness.toolsets.interaction import ASK_USER_QUESTION_TOOL_NAME
from pydantic import JsonValue, TypeAdapter
from pydantic_ai import ToolDenied, ToolFailed
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_service.runs.schemas import Approve, Complete, NoResponse, Pending, PendingItem, Reject, Resume

_REQUESTS = TypeAdapter(DeferredToolRequests)


def dump(requests: DeferredToolRequests) -> JsonValue:
    return _REQUESTS.dump_python(requests, mode="json")


def load(value: JsonValue) -> DeferredToolRequests:
    return _REQUESTS.validate_python(value)


def pending(requests: DeferredToolRequests) -> Pending:
    """External calls are user questions or client tools: the only external tools an agent declares."""
    items = [
        PendingItem(
            tool_call_id=part.tool_call_id,
            kind="approval",
            tool_name=part.tool_name,
            arguments=part.args_as_dict(),
            presentation=requests.metadata.get(part.tool_call_id, {}).get(APPROVAL_PRESENTATION_KEY),
        )
        for part in requests.approvals
    ]
    items.extend(
        PendingItem(
            tool_call_id=part.tool_call_id,
            kind="user_input" if part.tool_name == ASK_USER_QUESTION_TOOL_NAME else "client_tool",
            tool_name=part.tool_name,
            arguments=part.args_as_dict(),
        )
        for part in requests.calls
    )
    return Pending(items=tuple(items))


def resume(requests: DeferredToolRequests, answers: Resume) -> DeferredToolResume:
    """`answers` holds exactly one normalized answer per pending call."""
    results = DeferredToolResults()
    for answer in answers.answers:
        match answer:
            case Approve():
                results.approvals[answer.tool_call_id] = True
            case Reject():
                results.approvals[answer.tool_call_id] = ToolDenied(answer.reason or "The call was rejected")
            case Complete():
                results.calls[answer.tool_call_id] = answer.result
            case NoResponse():
                results.calls[answer.tool_call_id] = ToolFailed("No response was given")
    return DeferredToolResume(requests=requests, results=results)

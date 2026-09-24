"""Content-free host checks for one native model invocation."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

from a13n_harness.errors import RunError

if TYPE_CHECKING:
    from pydantic_ai.models import ModelRequestContext

    from a13n_harness.context import AgentContext


class ModelCallCheckError(RunError):
    """An authoritative host refusal; optional auxiliary work must not soften it."""

    def __init__(self, message: str = "Host model-call check refused dispatch.") -> None:
        super().__init__(message, code="model_call_check_failed")


@dataclass(frozen=True, slots=True)
class ModelCall:
    call_id: str
    harness_run_id: str
    model_run_id: str | None
    agent_instance_id: str
    parent_agent_instance_id: str | None
    delegation_id: str | None
    model_id: str | None
    model_name: str
    provider_name: str
    source: str
    tool_id: str | None
    tool_call_id: str | None


@runtime_checkable
class ModelCallCheck(Protocol):
    async def check(self, call: ModelCall) -> None:
        """Return to permit dispatch; raising or cancellation prevents it."""
        ...


async def _check_model_call(
    owner: "AgentContext",
    request: "ModelRequestContext",
    *,
    model_run_id: str | None,
    source: str,
    tool_id: str | None = None,
    tool_call_id: str | None = None,
) -> ModelCall:
    """Allocate once at the native wrapper boundary, after request preparation."""
    call = ModelCall(
        call_id=f"call_{uuid4().hex}",
        harness_run_id=owner.run_id,
        model_run_id=model_run_id,
        agent_instance_id=owner.instance.agent_instance_id,
        parent_agent_instance_id=owner.instance.parent_agent_instance_id,
        delegation_id=owner.instance.delegation_id,
        model_id=request.model_id,
        model_name=request.model.model_name,
        provider_name=request.model.system,
        source=source,
        tool_id=tool_id,
        tool_call_id=tool_call_id,
    )
    if owner.model_call_check is not None:
        try:
            await owner.model_call_check.check(call)
        except asyncio.CancelledError as error:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            # A cancelled borrowed check is a veto, not cancellation of the native runner task.
            raise ModelCallCheckError("Host model-call check was cancelled.") from error
        except Exception as error:
            raise ModelCallCheckError() from error
    return call

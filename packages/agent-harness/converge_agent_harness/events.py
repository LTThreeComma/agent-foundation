"""Ordered process-local Harness event envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic_ai.messages import AgentStreamEvent

from converge_agent_harness.result import HarnessRunResult


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """A public Pydantic AI stream event correlated to one Harness run."""

    run_id: str
    sequence: int
    occurred_at: datetime
    event: AgentStreamEvent


@dataclass(frozen=True, slots=True)
class HarnessRunResultEvent[OutputT]:
    """The sole terminal stream item, emitted only after teardown succeeds."""

    run_id: str
    sequence: int
    occurred_at: datetime
    result: HarnessRunResult[OutputT]


type HarnessStreamItem[OutputT] = HarnessEvent | HarnessRunResultEvent[OutputT]

"""Run-scoped Environment boundary and its no-operation zero value."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from converge_agent_harness.errors import HarnessError

if TYPE_CHECKING:
    from converge_agent_harness.identity import AgentInstanceContext


class EnvironmentError(HarnessError):
    """Environment binding or lifecycle failed."""


class BoundEnvironment(ABC):
    """Stable Environment facade available for one Harness run."""

    @property
    @abstractmethod
    def is_noop(self) -> bool:
        """Whether this facade intentionally exposes no operations."""


@dataclass(frozen=True, slots=True)
class NoopBoundEnvironment(BoundEnvironment):
    """A valid bound Environment with no operation surface."""

    @property
    def is_noop(self) -> bool:
        return True


class EnvironmentRunBinding(ABC):
    """Single-use factory for a run-scoped Environment facade."""

    @abstractmethod
    def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AbstractAsyncContextManager[BoundEnvironment]:
        """Bind and enter the Environment for exactly one run."""


@dataclass(slots=True)
class NoopEnvironmentRunBinding(EnvironmentRunBinding):
    """Single-use no-operation Environment binding."""

    _used: bool = field(default=False, init=False, repr=False)

    @asynccontextmanager
    async def bind(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
    ) -> AsyncGenerator[BoundEnvironment]:
        del run_id, instance
        if self._used:
            raise EnvironmentError(
                "EnvironmentRunBinding instances are single-use.",
                code="environment_binding_reused",
            )
        self._used = True
        yield NoopBoundEnvironment()

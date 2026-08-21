"""Converge Agent Harness public code-first API."""

from importlib.metadata import version

from converge_agent_harness.context import AgentContext, RunBindings, SubagentCollection
from converge_agent_harness.environment import (
    BoundEnvironment,
    EnvironmentError,
    EnvironmentRunBinding,
    NoopBoundEnvironment,
    NoopEnvironmentRunBinding,
)
from converge_agent_harness.errors import (
    DefinitionError,
    HarnessError,
    IdentityError,
    InputError,
    PluginError,
    RunCleanupError,
    RunError,
    StateError,
)
from converge_agent_harness.events import HarnessEvent, HarnessRunResultEvent, HarnessStreamItem
from converge_agent_harness.execution import AgentDefinition, ExecutableAgent, HarnessBuilder, HarnessRunStream
from converge_agent_harness.identity import AgentIdentityRef, AgentInstanceContext, AgentInstanceRef
from converge_agent_harness.input import (
    NativeRunInput,
    RunInputFactory,
    RunInputValue,
    RunPreparationContext,
    SemanticRunInput,
)
from converge_agent_harness.plugins import (
    AbstractHarnessPlugin,
    BoundPluginContext,
    PluginOrdering,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
)
from converge_agent_harness.result import HarnessRunResult, SafeFailure
from converge_agent_harness.state import (
    AgentContextState,
    AgentContextStateSnapshot,
    CapabilityState,
    HarnessState,
)

__version__ = version("converge-agent-harness")

__all__ = [
    "AbstractHarnessPlugin",
    "AgentContext",
    "AgentContextState",
    "AgentContextStateSnapshot",
    "AgentDefinition",
    "AgentIdentityRef",
    "AgentInstanceContext",
    "AgentInstanceRef",
    "BoundEnvironment",
    "BoundPluginContext",
    "CapabilityState",
    "DefinitionError",
    "EnvironmentError",
    "EnvironmentRunBinding",
    "ExecutableAgent",
    "HarnessBuilder",
    "HarnessError",
    "HarnessEvent",
    "HarnessRunResult",
    "HarnessRunResultEvent",
    "HarnessRunStream",
    "HarnessState",
    "HarnessStreamItem",
    "IdentityError",
    "InputError",
    "NativeRunInput",
    "NoopBoundEnvironment",
    "NoopEnvironmentRunBinding",
    "PluginError",
    "PluginOrdering",
    "PluginRunExchange",
    "PluginRunNext",
    "PluginRunResponse",
    "RunBindings",
    "RunCleanupError",
    "RunError",
    "RunInputFactory",
    "RunInputValue",
    "RunPreparationContext",
    "SafeFailure",
    "SemanticRunInput",
    "StateError",
    "SubagentCollection",
    "__version__",
]

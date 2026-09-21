"""Typed contracts for two-phase Agent invocation resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from a13n_harness.toolsets.file_media import NativeInputMediaKind

from a13n_service.connectivity.selection_domain import (
    ConnectionRunSelection,
)
from a13n_service.models.runtime import PreparedModelExecution
from a13n_service.skills.domain import SkillRevisionLock

from ..domain import (
    EffectiveAgentConfig,
    ResolvedSubagentEdge,
)
from ..invocation import MergedAgentRunConfig


class AgentSelectorKind(StrEnum):
    current = "current"
    exact = "exact"
    configuration = "configuration"


class RootAgentStatePolicy(StrEnum):
    invocable = "invocable"
    disabled_allowed = "disabled_allowed"


@dataclass(frozen=True, slots=True)
class PreparedChildInvocation:
    edge: ResolvedSubagentEdge
    invocation: PreparedAgentInvocation


@dataclass(frozen=True, slots=True)
class PreparedAgentInvocation:
    organization_id: str
    agent_id: str
    agent_revision_id: str | None
    selector_kind: AgentSelectorKind
    revision_content_digest: str | None
    merged: MergedAgentRunConfig
    model: PreparedModelExecution
    skills: tuple[SkillRevisionLock, ...]
    subagents: tuple[PreparedChildInvocation, ...]
    connectivity: tuple[ConnectionRunSelection, ...]
    media_models: dict[NativeInputMediaKind, PreparedModelExecution] = field(default_factory=dict)
    reviewer_model: PreparedModelExecution | None = None


@dataclass(frozen=True, slots=True)
class FrozenAgentInvocation:
    agent_id: str
    agent_revision_id: str | None
    selector_kind: AgentSelectorKind
    effective_config: EffectiveAgentConfig
    connection_selections: tuple[ConnectionRunSelection, ...]

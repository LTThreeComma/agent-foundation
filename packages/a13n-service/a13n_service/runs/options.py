"""Normalized option matching preserves the run's complete frozen caller context."""

from a13n_service.resources.agents.schemas import AgentConfig
from a13n_service.resources.connections.scope import effective_headers
from a13n_service.runs.schemas import RunOptions


def compatible(config: AgentConfig, left: RunOptions, right: RunOptions) -> bool:
    return left.model_dump(exclude={"mcp_headers"}) == right.model_dump(exclude={"mcp_headers"}) and effective_headers(
        config, left.mcp_headers
    ) == effective_headers(config, right.mcp_headers)

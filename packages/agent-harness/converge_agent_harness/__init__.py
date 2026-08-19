"""Converge Agent Harness."""

from converge_logging import get_logger

logger = get_logger(__name__)


def hello() -> str:
    """Return the package greeting."""
    logger.debug("harness_hello")
    return "Hello from converge-agent-harness!"

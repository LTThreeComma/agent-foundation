"""Low-level Python client package for the Agent Environment Interaction Protocol."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("converge-agent-envd-client")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

__all__ = ["__version__"]

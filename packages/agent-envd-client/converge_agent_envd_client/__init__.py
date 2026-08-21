"""Low-level Python client package for the Agent Environment Interaction Protocol."""

from importlib.metadata import PackageNotFoundError, version

from .errors import (
    EIPClientError,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSessionStateError,
    EIPTransportClosedError,
    EIPTransportError,
)
from .requester import RequestCoordinator
from .session import EIPSession
from .stdio import StdioTransport
from .transport import EIPTransport

try:
    __version__ = version("converge-agent-envd-client")
except PackageNotFoundError:  # pragma: no cover - source-tree imports without installation
    __version__ = "0.0.0"

__all__ = [
    "EIPClientError",
    "EIPMethodError",
    "EIPProtocolError",
    "EIPRequestTimeoutError",
    "EIPSession",
    "EIPSessionStateError",
    "EIPTransport",
    "EIPTransportClosedError",
    "EIPTransportError",
    "RequestCoordinator",
    "StdioTransport",
    "__version__",
]

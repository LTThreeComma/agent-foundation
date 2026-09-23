"""The endpoints environment accounts name that Service processes dial themselves.

The outbound endpoint policy checks each before it is dialed. An account that names none reaches only a backend
the operator chose.
"""

from urllib.parse import urlsplit

from a13n_harness.providers.environment.docker.provider import DockerConnectionConfiguration
from a13n_harness.providers.environment.remote_envd.configuration import HttpEnvdConnectionConfiguration
from pydantic import BaseModel

# A remote Docker engine's scheme, and the one its API is spoken over.
_REMOTE_ENGINE = {"tcp": "http", "https": "https"}


def engine_endpoint(docker_host: str) -> str:
    """The URL of a remote Docker engine; a unix socket or SSH would reach a host the operator owns."""
    try:
        parts = urlsplit(docker_host)
        port = parts.port
    except ValueError:
        raise ValueError("docker_host is not a valid URL") from None
    if (
        parts.scheme not in _REMOTE_ENGINE
        or not parts.hostname
        or port is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("docker_host must be tcp://host:port or https://host:port")
    return f"{_REMOTE_ENGINE[parts.scheme]}://{parts.netloc}"


def dialed_endpoint(configuration: BaseModel) -> str | None:
    """The URL an account names: an HTTP envd device, or a Docker engine other than the operator's."""
    if isinstance(configuration, HttpEnvdConnectionConfiguration):
        return configuration.endpoint
    if isinstance(configuration, DockerConnectionConfiguration) and "docker_host" in configuration.model_fields_set:
        return engine_endpoint(configuration.docker_host)
    return None

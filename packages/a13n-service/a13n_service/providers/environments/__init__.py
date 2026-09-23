"""The environment provider types the Service offers, each qualified individually.

`docker` is the managed backend under the operator's engine and host directory choices, `http_envd` the
connect-only registered device, and `local` a development-only managed directory on the worker host. Other
Harness types are not advertised until their lifecycle recovery is demonstrated.
"""

from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.environment.remote_envd.http import HTTP_ENVD

from a13n_service.providers.environments.docker import DOCKER, docker
from a13n_service.providers.environments.local import LOCAL

BUILT_IN_ENVIRONMENT_PROVIDERS = (DOCKER, HTTP_ENVD, LOCAL)


def offered(
    definitions: Iterable[ProviderDefinition],
    *,
    allow_local: bool,
    docker_host: str | None,
    docker_mount_roots: Sequence[PurePosixPath],
) -> tuple[ProviderDefinition, ...]:
    """The definitions a deployment registers: `local` is no isolation boundary, so only development offers it,
    and `docker` follows the operator's engine and host directory choices."""
    configured = docker(host=docker_host, mount_roots=docker_mount_roots)
    return tuple(
        configured if definition is DOCKER else definition
        for definition in definitions
        if allow_local or definition is not LOCAL
    )

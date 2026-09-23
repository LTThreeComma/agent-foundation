"""The process-wide dependencies run operations use, assembled once by `app.py`."""

from dataclasses import dataclass
from functools import partial

from a13n_harness.providers.endpoint_policy import EndpointPolicy
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, after_commit
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.infra.redis import wake
from a13n_service.providers.registry import Registry
from a13n_service.runs.admission import AdmissionPolicy
from a13n_service.settings import Settings


@dataclass(frozen=True)
class Runtime:
    storage: Storage
    objects: ObjectStore
    redis: Redis
    keys: KeyRing
    settings: Settings
    registry: Registry
    admission: AdmissionPolicy | None = None

    @property
    def endpoint_policy(self) -> EndpointPolicy:
        """Which provider and connection endpoints outbound requests may reach."""
        providers = self.settings.providers
        return EndpointPolicy.from_operator_allowlist(
            private_domains=providers.private_domains,
            private_cidrs=providers.private_cidrs,
            http_origins=providers.http_origins,
            require_https=providers.require_https,
        )

    def wake_workers(self, session: AsyncSession) -> None:
        """After commit, tell idle workers to scan now; the periodic scan covers a lost wakeup."""
        after_commit(session, partial(wake, self.redis, timeout=self.settings.redis.timeout))

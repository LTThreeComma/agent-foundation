"""The provider definitions a deployment selected, keyed by (kind, type).

Definitions are the Harness's own, so their schemas validate resource `config` and `credential` exactly as
execution will; the Service adds no second implementation catalogue.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.connector.definition import ConnectorProviderDefinition
from a13n_harness.providers.definition import ProviderDefinition
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition
from a13n_harness.providers.model.definition import ModelProviderDefinition
from a13n_harness.providers.web.definition import WebProviderDefinition

from a13n_service.infra.errors import ServiceError

type ProviderKind = Literal["model", "environment", "connector", "web"]

_KINDS: dict[str, ProviderKind] = {
    "Model": "model",
    "Environment": "environment",
    "Connector": "connector",
    "Web": "web",
}


@dataclass(frozen=True)
class Registry:
    models: ProviderCatalog[ModelProviderDefinition]
    environments: ProviderCatalog[EnvironmentProviderDefinition]
    connectors: ProviderCatalog[ConnectorProviderDefinition]
    web: ProviderCatalog[WebProviderDefinition]

    @classmethod
    def of(cls, definitions: Iterable[ProviderDefinition]) -> "Registry":
        grouped: dict[ProviderKind, list[ProviderDefinition]] = {kind: [] for kind in _KINDS.values()}
        for definition in definitions:
            if definition.DOMAIN not in _KINDS:
                raise ValueError(f"Unsupported provider domain: {definition.DOMAIN}")
            grouped[_KINDS[definition.DOMAIN]].append(definition)
        return cls(
            models=ProviderCatalog(grouped["model"]),  # type: ignore[arg-type]
            environments=ProviderCatalog(grouped["environment"]),  # type: ignore[arg-type]
            connectors=ProviderCatalog(grouped["connector"]),  # type: ignore[arg-type]
            web=ProviderCatalog(grouped["web"]),  # type: ignore[arg-type]
        )

    def catalog(self, kind: ProviderKind) -> ProviderCatalog:
        return {"model": self.models, "environment": self.environments, "connector": self.connectors, "web": self.web}[
            kind
        ]

    def get(self, kind: ProviderKind, type_: str) -> ProviderDefinition:
        definition = self.catalog(kind).get(type_)
        if definition is None:
            raise ServiceError("unavailable", "Provider type is not available", {"dependency": f"{kind}:{type_}"})
        return definition

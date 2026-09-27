"""Environment creation addresses resolved before reserving an instance."""

from a13n_service.resources.references import Reference
from a13n_service.runs.environments.schemas import ManagedEnvironmentFields


class ManagedEnvironmentInput(ManagedEnvironmentFields):
    template: Reference

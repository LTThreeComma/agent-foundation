"""Ordinary-only execution composition for Service tests."""

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_service_legacy.memory.behaviors import MemoryBehaviors
from a13n_service_legacy.memory.ordinary import OrdinaryMemory
from a13n_service_legacy.memory.scopes import MemoryAuthorizer
from a13n_service_legacy.memory.service import MemoryService
from a13n_service_legacy.secrets import SecretProtector


def ordinary_memory(sessions):
    catalog = ProviderCatalog(())
    service = MemoryService(
        catalog, SecretProtector(key=b"k" * 32, encryption_key_id="test"), MemoryAuthorizer(sessions, catalog)
    )
    return MemoryBehaviors(sessions, default=OrdinaryMemory(service))

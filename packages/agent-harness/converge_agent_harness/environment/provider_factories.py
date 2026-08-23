"""Explicit package discovery for trusted Environment provider factories."""

from __future__ import annotations

import importlib.metadata
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from converge_agent_harness._json import require_finite_json

from .models import EnvironmentError
from .providers import EnvironmentProviderBinding

ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP = "converge_agent_harness.environments"
_MAX_PROVIDER_KEY_LENGTH = 200
_MAX_DIAGNOSTIC_VALUE_LENGTH = 200
_CONFIGURATION_ADAPTER = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryReference:
    """Installed entry-point metadata without importing its target."""

    provider_key: str
    import_target: str
    distribution_name: str | None
    distribution_version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentProviderFactoryRegistration:
    """One validated provider factory and its process-local provenance."""

    provider_key: str
    class_module: str
    class_qualname: str
    import_target: str | None
    distribution_name: str | None
    distribution_version: str | None


class EnvironmentProviderFactory(ABC):
    """Trusted factory for one pre-entry-inert provider binding."""

    @classmethod
    @abstractmethod
    def provider_key(cls) -> str:
        """Return the stable Host-facing provider key."""

    @abstractmethod
    def create_provider_binding(
        self,
        configuration: Mapping[str, JsonValue],
    ) -> EnvironmentProviderBinding:
        """Create one fresh provider binding without acquiring external resources."""


class EnvironmentProviderFactoryCatalog(Mapping[str, EnvironmentProviderFactory]):
    """Immutable caller-owned snapshot of selected Environment provider factories."""

    __slots__ = ("_factories", "_registrations")

    def __init__(
        self,
        entries: Sequence[tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]],
    ) -> None:
        registrations = tuple(registration for registration, _factory in entries)
        factories = {registration.provider_key: factory for registration, factory in entries}
        if len(factories) != len(entries):
            raise EnvironmentError(
                "Environment provider factory keys must be unique.",
                code="environment_provider_factory_duplicate",
            )
        self._registrations = registrations
        self._factories = MappingProxyType(factories)

    def __getitem__(self, provider_key: str) -> EnvironmentProviderFactory:
        return self._factories[provider_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._factories)

    def __len__(self) -> int:
        return len(self._factories)

    @property
    def registrations(self) -> tuple[EnvironmentProviderFactoryRegistration, ...]:
        """Return registrations in selected-then-explicit order."""

        return self._registrations

    def require(self, provider_key: str) -> EnvironmentProviderFactory:
        """Return one selected provider factory or fail with a stable Environment error."""

        key = _validate_provider_key(provider_key)
        factory = self._factories.get(key)
        if factory is None:
            raise EnvironmentError(
                "The required Environment provider factory is not selected.",
                code="environment_provider_factory_missing",
                details={"provider_key": key},
            )
        return factory

    def create_provider_binding(
        self,
        provider_key: str,
        configuration: Mapping[str, JsonValue],
    ) -> EnvironmentProviderBinding:
        """Invoke one selected factory and validate its public return boundary."""

        key = _validate_provider_key(provider_key)
        factory = self.require(key)
        try:
            detached_configuration = _CONFIGURATION_ADAPTER.validate_python(configuration)
            require_finite_json(detached_configuration)
        except (ValidationError, ValueError) as exc:
            raise EnvironmentError(
                "Environment provider factory configuration must be a JSON object.",
                code="environment_provider_factory_configuration_invalid",
                details={"provider_key": key},
            ) from exc
        try:
            binding = factory.create_provider_binding(detached_configuration)
        except Exception as exc:
            raise EnvironmentError(
                "Environment provider factory failed.",
                code="environment_provider_factory_failed",
                details={"provider_key": key},
            ) from exc
        if not isinstance(binding, EnvironmentProviderBinding):
            raise EnvironmentError(
                "Environment provider factory returned an invalid provider binding.",
                code="environment_provider_factory_result_invalid",
                details={"provider_key": key},
            )
        return binding


def discover_environment_provider_factory_references() -> tuple[EnvironmentProviderFactoryReference, ...]:
    """Discover deterministic provider-factory metadata without importing target code."""

    references = tuple(_entry_point_reference(entry_point) for entry_point in _entry_points())
    return tuple(
        sorted(
            references,
            key=lambda item: (
                item.provider_key,
                item.distribution_name or "",
                item.distribution_version or "",
                item.import_target,
            ),
        )
    )


def build_environment_provider_factory_catalog(
    *,
    provider_keys: Iterable[str] = (),
    explicit_factories: Iterable[EnvironmentProviderFactory] = (),
) -> EnvironmentProviderFactoryCatalog:
    """Load an immutable catalog without importing unselected entry points."""

    selected = tuple(_validate_provider_key(value) for value in provider_keys)
    _require_unique_keys(selected)

    explicit_entries: list[tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]] = []
    explicit_keys: set[str] = set()
    for factory in explicit_factories:
        if not isinstance(factory, EnvironmentProviderFactory):
            raise EnvironmentError(
                "Explicit Environment provider factories must implement EnvironmentProviderFactory.",
                code="environment_provider_factory_target_invalid",
            )
        key = _provider_key(factory)
        if key in explicit_keys:
            raise EnvironmentError(
                "An Environment provider factory key was supplied more than once.",
                code="environment_provider_factory_duplicate",
                details={"provider_key": key},
            )
        explicit_keys.add(key)
        explicit_entries.append((_explicit_registration(key, factory), factory))

    collisions = sorted(set(selected) & explicit_keys)
    if collisions:
        raise EnvironmentError(
            "Selected and explicit Environment provider factories have colliding keys.",
            code="environment_provider_factory_duplicate",
            details={"provider_key": collisions[0]},
        )

    selected_entries: list[tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]] = []
    if selected:
        discovered: dict[str, list[importlib.metadata.EntryPoint]] = {}
        selected_set = set(selected)
        for entry_point in _entry_points():
            if entry_point.name in selected_set:
                discovered.setdefault(entry_point.name, []).append(entry_point)

        missing = [key for key in selected if key not in discovered]
        if missing:
            raise EnvironmentError(
                "A selected Environment provider factory entry point was not found.",
                code="environment_provider_factory_missing",
                details={"provider_key": missing[0]},
            )
        for key in selected:
            matches = discovered[key]
            if len(matches) != 1:
                raise EnvironmentError(
                    "An Environment provider factory key is supplied by more than one distribution.",
                    code="environment_provider_factory_duplicate",
                    details={"provider_key": key},
                )

        for key in selected:
            selected_entries.append(_load_entry_point(key, discovered[key][0]))

    return EnvironmentProviderFactoryCatalog((*selected_entries, *explicit_entries))


def _entry_points() -> tuple[importlib.metadata.EntryPoint, ...]:
    return tuple(importlib.metadata.entry_points(group=ENVIRONMENT_PROVIDER_ENTRY_POINT_GROUP))


def _entry_point_reference(entry_point: importlib.metadata.EntryPoint) -> EnvironmentProviderFactoryReference:
    distribution = entry_point.dist
    distribution_name: str | None = None
    distribution_version: str | None = None
    if distribution is not None:
        name = distribution.metadata.get("Name")
        if isinstance(name, str) and name:
            distribution_name = name
        version = distribution.version
        if isinstance(version, str) and version:
            distribution_version = version
    return EnvironmentProviderFactoryReference(
        provider_key=entry_point.name,
        import_target=entry_point.value,
        distribution_name=distribution_name,
        distribution_version=distribution_version,
    )


def _load_entry_point(
    provider_key: str,
    entry_point: importlib.metadata.EntryPoint,
) -> tuple[EnvironmentProviderFactoryRegistration, EnvironmentProviderFactory]:
    reference = _entry_point_reference(entry_point)
    try:
        loaded = entry_point.load()
    except Exception as exc:
        raise EnvironmentError(
            "A selected Environment provider factory could not be loaded.",
            code="environment_provider_factory_load_failed",
            details=_reference_details(reference),
        ) from exc
    if not isinstance(loaded, type) or not issubclass(loaded, EnvironmentProviderFactory):
        raise EnvironmentError(
            "An Environment provider factory entry point must load an EnvironmentProviderFactory class.",
            code="environment_provider_factory_target_invalid",
            details=_reference_details(reference),
        )
    factory_type = cast(type[EnvironmentProviderFactory], loaded)
    try:
        factory = factory_type()
    except Exception as exc:
        raise EnvironmentError(
            "An Environment provider factory class must support safe no-argument construction.",
            code="environment_provider_factory_load_failed",
            details=_reference_details(reference),
        ) from exc
    actual_key = _provider_key(factory, reference=reference)
    if actual_key != provider_key:
        raise EnvironmentError(
            "Environment provider factory entry-point name and provider key do not match.",
            code="environment_provider_factory_key_invalid",
            details=_reference_details(reference),
        )
    registration = EnvironmentProviderFactoryRegistration(
        provider_key=provider_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=reference.import_target,
        distribution_name=reference.distribution_name,
        distribution_version=reference.distribution_version,
    )
    return registration, factory


def _provider_key(
    factory: EnvironmentProviderFactory,
    *,
    reference: EnvironmentProviderFactoryReference | None = None,
) -> str:
    try:
        return _validate_provider_key(factory.provider_key())
    except EnvironmentError as exc:
        details = _reference_details(reference) if reference is not None else None
        raise EnvironmentError(
            "Environment provider factory key is invalid.",
            code="environment_provider_factory_key_invalid",
            details=details,
        ) from exc
    except Exception as exc:
        details = _reference_details(reference) if reference is not None else None
        raise EnvironmentError(
            "Environment provider factory key could not be read.",
            code="environment_provider_factory_key_invalid",
            details=details,
        ) from exc


def _explicit_registration(
    provider_key: str,
    factory: EnvironmentProviderFactory,
) -> EnvironmentProviderFactoryRegistration:
    factory_type = type(factory)
    return EnvironmentProviderFactoryRegistration(
        provider_key=provider_key,
        class_module=factory_type.__module__,
        class_qualname=factory_type.__qualname__,
        import_target=None,
        distribution_name=None,
        distribution_version=None,
    )


def _validate_provider_key(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > _MAX_PROVIDER_KEY_LENGTH:
        raise EnvironmentError(
            "Environment provider factory keys must be bounded non-blank strings without surrounding whitespace.",
            code="environment_provider_factory_key_invalid",
        )
    return value


def _require_unique_keys(keys: Sequence[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise EnvironmentError(
                "An Environment provider factory key was selected more than once.",
                code="environment_provider_factory_duplicate",
                details={"provider_key": key},
            )
        seen.add(key)


def _reference_details(reference: EnvironmentProviderFactoryReference) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "provider_key": reference.provider_key[:_MAX_DIAGNOSTIC_VALUE_LENGTH],
    }
    if reference.distribution_name is not None:
        details["distribution_name"] = reference.distribution_name[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    if reference.distribution_version is not None:
        details["distribution_version"] = reference.distribution_version[:_MAX_DIAGNOSTIC_VALUE_LENGTH]
    return details

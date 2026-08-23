from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from converge_agent_harness import (
    EnvironmentError,
    EnvironmentProviderBinding,
    EnvironmentProviderFactory,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)


class _Binding(EnvironmentProviderBinding):
    def __init__(self, name: str) -> None:
        self.name = name
        self.discarded = False

    @property
    def provider_type(self) -> str:
        return "test.plugin"

    @property
    def environment_id(self) -> str:
        return f"environment:{self.name}"

    @asynccontextmanager
    async def bind(self, **kwargs: Any):
        del kwargs
        yield object()

    async def discard(self) -> None:
        self.discarded = True


class _Factory(EnvironmentProviderFactory):
    calls: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def provider_key(cls) -> str:
        return "test.plugin"

    def create_provider_binding(self, configuration):
        detached = dict(configuration)
        self.calls.append(detached)
        return _Binding(str(detached.get("name", "default")))


class _OtherFactory(_Factory):
    @classmethod
    def provider_key(cls) -> str:
        return "test.other"


class _MismatchedFactory(_Factory):
    @classmethod
    def provider_key(cls) -> str:
        return "wrong.key"


class _InvalidBindingFactory(_Factory):
    def create_provider_binding(self, configuration):
        del configuration
        return object()


class _FailingFactory(_Factory):
    def create_provider_binding(self, configuration):
        del configuration
        raise RuntimeError("secret factory detail")


class _FakeEntryPoint:
    def __init__(
        self,
        name: str,
        target: object,
        *,
        value: str | None = None,
        distribution: str = "test-environment-plugin",
        version: str = "1.2.3",
    ) -> None:
        self.name = name
        self.value = value or f"test_environment:{getattr(target, '__name__', 'target')}"
        self.dist = SimpleNamespace(metadata={"Name": distribution}, version=version)
        self._target = target
        self.load_count = 0

    def load(self) -> object:
        self.load_count += 1
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


def test_discovery_reads_metadata_without_importing_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _FakeEntryPoint("test.plugin", _Factory)
    second = _FakeEntryPoint("test.other", _OtherFactory, distribution="other-plugin", version="2.0")
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (second, first),
    )

    references = discover_environment_provider_factory_references()

    assert [reference.provider_key for reference in references] == ["test.other", "test.plugin"]
    assert references[1].distribution_name == "test-environment-plugin"
    assert references[1].distribution_version == "1.2.3"
    assert first.load_count == second.load_count == 0


def test_empty_selection_does_not_scan_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_scan() -> tuple[()]:
        raise AssertionError("empty selection must not scan installed metadata")

    monkeypatch.setattr("converge_agent_harness.environment.provider_factories._entry_points", reject_scan)

    assert len(build_environment_provider_factory_catalog()) == 0


def test_catalog_loads_only_selected_target_and_records_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    selected = _FakeEntryPoint("test.plugin", _Factory)
    unselected = _FakeEntryPoint("test.other", RuntimeError("must not load"))
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (unselected, selected),
    )

    catalog = build_environment_provider_factory_catalog(provider_keys=("test.plugin",))

    assert list(catalog) == ["test.plugin"]
    assert isinstance(catalog.require("test.plugin"), _Factory)
    assert selected.load_count == 1
    assert unselected.load_count == 0
    registration = catalog.registrations[0]
    assert registration.provider_key == "test.plugin"
    assert registration.distribution_name == "test-environment-plugin"
    assert registration.distribution_version == "1.2.3"
    assert registration.import_target == selected.value


def test_explicit_factories_need_no_metadata_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    catalog = build_environment_provider_factory_catalog(explicit_factories=(_Factory(),))

    assert isinstance(catalog["test.plugin"], _Factory)
    assert catalog.registrations[0].import_target is None


@pytest.mark.parametrize(
    ("selected", "entries", "code"),
    [
        (("missing.plugin",), (), "environment_provider_factory_missing"),
        (
            ("test.plugin",),
            (_FakeEntryPoint("test.plugin", _Factory), _FakeEntryPoint("test.plugin", _Factory)),
            "environment_provider_factory_duplicate",
        ),
        (("test.plugin", "test.plugin"), (), "environment_provider_factory_duplicate"),
    ],
)
def test_catalog_rejects_missing_and_duplicate_selection(
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[str, ...],
    entries: tuple[_FakeEntryPoint, ...],
    code: str,
) -> None:
    monkeypatch.setattr("converge_agent_harness.environment.provider_factories._entry_points", lambda: entries)

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_provider_factory_catalog(provider_keys=selected)

    assert exc_info.value.code == code


def test_catalog_preflights_explicit_collision_before_import(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _Factory)
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_provider_factory_catalog(
            provider_keys=("test.plugin",),
            explicit_factories=(_Factory(),),
        )

    assert exc_info.value.code == "environment_provider_factory_duplicate"
    assert entry_point.load_count == 0


@pytest.mark.parametrize("target", [object(), object])
def test_catalog_rejects_invalid_entry_point_target(
    monkeypatch: pytest.MonkeyPatch,
    target: object,
) -> None:
    entry_point = _FakeEntryPoint("test.plugin", target)
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_provider_factory_catalog(provider_keys=("test.plugin",))

    assert exc_info.value.code == "environment_provider_factory_target_invalid"


def test_catalog_rejects_mismatched_provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_point = _FakeEntryPoint("test.plugin", _MismatchedFactory)
    monkeypatch.setattr(
        "converge_agent_harness.environment.provider_factories._entry_points",
        lambda: (entry_point,),
    )

    with pytest.raises(EnvironmentError) as exc_info:
        build_environment_provider_factory_catalog(provider_keys=("test.plugin",))

    assert exc_info.value.code == "environment_provider_factory_key_invalid"


def test_catalog_validates_factory_configuration_and_binding() -> None:
    _Factory.calls.clear()
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_Factory(),))
    configuration = {"name": "first", "nested": {"values": [1]}}

    binding = catalog.create_provider_binding("test.plugin", configuration)
    configuration["nested"]["values"].append(2)

    assert isinstance(binding, _Binding)
    assert binding.name == "first"
    assert _Factory.calls == [{"name": "first", "nested": {"values": [1]}}]


@pytest.mark.parametrize(
    "configuration",
    [
        {"value": object()},
        {"value": float("nan")},
        {"value": float("inf")},
    ],
)
def test_catalog_rejects_non_json_factory_configuration(configuration: dict[str, Any]) -> None:
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_Factory(),))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_provider_binding("test.plugin", configuration)

    assert exc_info.value.code == "environment_provider_factory_configuration_invalid"


def test_catalog_rejects_invalid_factory_result() -> None:
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_InvalidBindingFactory(),))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_provider_binding("test.plugin", {})

    assert exc_info.value.code == "environment_provider_factory_result_invalid"


def test_catalog_sanitizes_factory_failure() -> None:
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_FailingFactory(),))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.create_provider_binding("test.plugin", {"token": "do-not-render"})

    assert exc_info.value.code == "environment_provider_factory_failed"
    assert "secret" not in str(exc_info.value)
    assert "token" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_catalog_require_rejects_unselected_key() -> None:
    catalog = build_environment_provider_factory_catalog(explicit_factories=(_Factory(),))

    with pytest.raises(EnvironmentError) as exc_info:
        catalog.require("missing.plugin")

    assert exc_info.value.code == "environment_provider_factory_missing"

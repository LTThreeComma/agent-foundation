from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "check-agent-distributions.py"


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_agent_distributions", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3rc4"])
def test_release_validation_requires_exact_same_version_harness(version: str) -> None:
    checker = load_checker()

    checker._validate_protocol_requirement(
        [f"converge-agent-harness=={version}", "pydantic>=2.12"],
        version,
        Path("protocol.whl"),
        require_exact_internal_version=True,
    )


def test_development_validation_accepts_unpinned_workspace_requirement() -> None:
    checker = load_checker()

    checker._validate_protocol_requirement(
        ["converge-agent-harness", "pydantic>=2.12"],
        "0.0.0",
        Path("protocol.whl"),
        require_exact_internal_version=False,
    )


def test_release_validation_rejects_unpinned_requirement() -> None:
    checker = load_checker()

    with pytest.raises(checker.DistributionError, match=r"converge-agent-harness==1\.2\.3"):
        checker._validate_protocol_requirement(
            ["converge-agent-harness"],
            "1.2.3",
            Path("protocol.whl"),
            require_exact_internal_version=True,
        )


def test_development_validation_rejects_published_exact_requirement() -> None:
    checker = load_checker()

    with pytest.raises(checker.DistributionError, match="Expected converge-agent-harness "):
        checker._validate_protocol_requirement(
            ["converge-agent-harness==0.0.0"],
            "0.0.0",
            Path("protocol.whl"),
            require_exact_internal_version=False,
        )

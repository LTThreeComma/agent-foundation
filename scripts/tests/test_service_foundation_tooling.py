"""Archive routing and shared tracing infrastructure remain independent."""

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from dev.observability import langfuse
from dev.service import __main__ as service_dev
from scripts import impact, verify

ROOT = Path(__file__).resolve().parents[2]


def test_legacy_is_not_a_workspace_or_validation_input():
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "packages/a13n-service-legacy" in manifest["tool"]["uv"]["workspace"]["exclude"]
    assert importlib.util.find_spec("a13n_service_legacy") is None
    assert "a13n-service-legacy" not in impact.packages_with_tests()
    graph = verify.PythonGraph(ROOT)
    assert not any("a13n-service-legacy" in str(path) for path in graph.modules)
    plan = verify.plan(["packages/a13n-service-legacy/a13n_service_legacy/cli.py"], graph)
    assert not plan.python_tests and not plan.python_files


def test_unimplemented_live_journeys_fail_explicitly():
    result = subprocess.run([sys.executable, "-m", "dev.live_tests"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert "not implemented" in result.stderr


def test_shared_langfuse_reuses_verified_manifest_and_preserves_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(langfuse, "_machine_directory", lambda: tmp_path)
    stack = langfuse.Langfuse()
    monkeypatch.setattr(stack, "_project_resources", lambda: ())
    compose = stack._shared_compose(initialize=True, require_compatible_source=True)
    assert compose is not None
    manifest = json.loads((tmp_path / "langfuse-v2.json").read_text())
    assert manifest["project"] == "agent-foundation-local-langfuse-v2"
    assert stack._shared_compose(initialize=False, require_compatible_source=False) == compose
    calls = []
    monkeypatch.setattr(langfuse.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stack, "_compose", lambda path, *args: calls.append(args))
    stack.stop()
    assert calls == [("down", "--remove-orphans")]
    assert compose.exists() and (tmp_path / "langfuse-v2.json").exists()
    compose.write_text("changed")
    with pytest.raises(ValueError, match="changed unexpectedly"):
        stack._shared_compose(initialize=False, require_compatible_source=False)


def test_shared_langfuse_uses_public_fixture_configuration(monkeypatch, tmp_path):
    seen = {}

    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr(langfuse.subprocess, "run", run)
    langfuse.Langfuse()._compose(tmp_path / "compose.yaml", "up", "-d")
    assert seen["env"]["LANGFUSE_LOCAL_PUBLIC_KEY"] == langfuse.PUBLIC_KEY
    assert seen["env"]["LANGFUSE_LOCAL_SECRET_KEY"] == langfuse.SECRET_KEY
    assert "--env-file" in seen["command"]
    assert seen["env"]["LANGFUSE_LOCAL_PORT"] == "3000"


def test_local_status_exposes_listener_and_database_without_credentials(tmp_path, monkeypatch, capsys):
    config = tmp_path / "var/service-rewrite/local.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[server]\nhost="127.0.0.1"\nport=8123\n[database]\nurl="postgresql+psycopg://user:fixture-secret-value@127.0.0.1:6543/foundation"\n'
    )
    monkeypatch.setattr(service_dev, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["service", "status"])
    monkeypatch.setattr(
        service_dev.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="127.0.0.1:6543\n"),
    )
    service_dev.main()
    output = capsys.readouterr().out
    status = json.loads(output)
    assert status["service"] == "http://127.0.0.1:8123"
    assert status["database"] == {"host": "127.0.0.1", "port": 6543, "name": "foundation"}
    assert "fixture-secret-value" not in output and "user:" not in output

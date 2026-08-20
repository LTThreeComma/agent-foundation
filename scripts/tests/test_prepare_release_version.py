from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
PREPARER = REPOSITORY_ROOT / "scripts" / "prepare-release-version.py"
CHECKER = REPOSITORY_ROOT / "scripts" / "check-release-version.py"
RELEASE_FILES = (
    Path("pyproject.toml"),
    Path("uv.lock"),
    Path("packages/agent-harness/pyproject.toml"),
    Path("packages/logging/pyproject.toml"),
    Path("packages/foundation-service/pyproject.toml"),
    Path("packages/agent-envd-client/pyproject.toml"),
    Path("Cargo.toml"),
    Path("Cargo.lock"),
    Path("crates/agent-envd/Cargo.toml"),
    Path("sdk/python/pyproject.toml"),
    Path("sdk/python/uv.lock"),
    Path("sdk/rust/Cargo.toml"),
    Path("sdk/rust/Cargo.lock"),
    Path("sdk/typescript/package.json"),
    Path("sdk/typescript/package-lock.json"),
)


def copy_release_files(destination: Path) -> None:
    for relative_path in RELEASE_FILES:
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY_ROOT / relative_path, target)


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path: (root / path).read_bytes() for path in RELEASE_FILES}


def run_script(
    script: Path,
    root: Path,
    component: str,
    version: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), component, version],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("component", "changed_paths"),
    [
        (
            "foundation",
            {
                Path("pyproject.toml"),
                Path("uv.lock"),
                Path("packages/agent-harness/pyproject.toml"),
                Path("packages/logging/pyproject.toml"),
                Path("packages/foundation-service/pyproject.toml"),
            },
        ),
        (
            "agent-envd",
            {
                Path("Cargo.toml"),
                Path("Cargo.lock"),
                Path("packages/agent-envd-client/pyproject.toml"),
                Path("uv.lock"),
            },
        ),
        (
            "sdk-python",
            {Path("sdk/python/pyproject.toml"), Path("sdk/python/uv.lock")},
        ),
        ("sdk-go", set()),
        (
            "sdk-rust",
            {Path("sdk/rust/Cargo.toml"), Path("sdk/rust/Cargo.lock")},
        ),
        (
            "sdk-typescript",
            {
                Path("sdk/typescript/package.json"),
                Path("sdk/typescript/package-lock.json"),
            },
        ),
    ],
)
def test_prepares_only_component_files_and_is_idempotent(
    tmp_path: Path,
    component: str,
    changed_paths: set[Path],
) -> None:
    copy_release_files(tmp_path)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, component, "9.8.7")

    assert result.returncode == 0, result.stderr
    after = snapshot(tmp_path)
    assert {path for path in RELEASE_FILES if before[path] != after[path]} == changed_paths

    check_result = run_script(CHECKER, tmp_path, component, "9.8.7")
    assert check_result.returncode == 0, check_result.stderr

    second_result = run_script(PREPARER, tmp_path, component, "9.8.7")
    assert second_result.returncode == 0, second_result.stderr
    assert snapshot(tmp_path) == after
    assert "no files changed" in second_result.stdout


def test_foundation_release_does_not_version_agent_envd_client(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "foundation", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "agent-envd", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_agent_envd_release_does_not_version_foundation_packages(tmp_path: Path) -> None:
    copy_release_files(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-envd", "9.8.7")

    assert result.returncode == 0, result.stderr
    check_result = run_script(CHECKER, tmp_path, "foundation", "0.0.0")
    assert check_result.returncode == 0, check_result.stderr


def test_rejects_invalid_version_without_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "foundation", "v1.2.3")

    assert result.returncode != 0
    assert "Release version must use X.Y.Z syntax" in result.stderr
    assert snapshot(tmp_path) == before


def test_validates_all_targets_before_writing(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    lock_path = tmp_path / "sdk/typescript/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["packages"][""]["version"]
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n", encoding="utf-8")
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "sdk-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'packages[""] version' in result.stderr
    assert snapshot(tmp_path) == before


def test_requires_agent_envd_workspace_version_inheritance(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    manifest_path = tmp_path / "crates/agent-envd/Cargo.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "version.workspace = true",
            'version = "0.0.0"',
        ),
        encoding="utf-8",
    )
    before = snapshot(tmp_path)

    result = run_script(PREPARER, tmp_path, "agent-envd", "9.8.7")

    assert result.returncode != 0
    assert "package.version.workspace = true" in result.stderr
    assert snapshot(tmp_path) == before


def test_checker_validates_nested_npm_lock_version(tmp_path: Path) -> None:
    copy_release_files(tmp_path)
    prepare_result = run_script(PREPARER, tmp_path, "sdk-typescript", "9.8.7")
    assert prepare_result.returncode == 0, prepare_result.stderr

    lock_path = tmp_path / "sdk/typescript/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"][""]["version"] = "9.8.6"
    lock_path.write_text(f"{json.dumps(lock, indent=2)}\n", encoding="utf-8")

    result = run_script(CHECKER, tmp_path, "sdk-typescript", "9.8.7")

    assert result.returncode != 0
    assert 'sdk/typescript/package-lock.json packages[""]: 9.8.6' in result.stderr

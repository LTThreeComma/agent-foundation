from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.eip_codegen.__main__ import (
    ARTIFACT_PATH,
    DESCRIPTOR_PATH,
    GENERATED_HEADER,
    MANIFEST_PATH,
    PYTHON_PATH,
    verify_generated,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
OPENRPC_PATH = REPOSITORY_ROOT / "proto/agent-envd/eip/v1/artifacts/openrpc.json"
SCHEMA_PATH = REPOSITORY_ROOT / "proto/agent-envd/eip/v1/artifacts/schema.json"


def write_generated_tree(root: Path) -> None:
    descriptor = b"test EIP descriptor"
    files = {
        DESCRIPTOR_PATH: descriptor,
        PYTHON_PATH / "models.py": f"{GENERATED_HEADER}VALUE = 1\n".encode(),
        ARTIFACT_PATH / "methods.json": b'{"generated":true}\n',
    }
    manifest_files = sorted([*(path.as_posix() for path in files), MANIFEST_PATH.as_posix()])
    manifest = {
        "generated": True,
        "descriptor_sha256": hashlib.sha256(descriptor).hexdigest(),
        "files": manifest_files,
    }
    files[MANIFEST_PATH] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def snapshot(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_checked_inspection_artifacts_follow_eip_json_profile() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]
    assert schema["EIPPath"]["properties"]["path"]["format"] == "eip-absolute-path"
    assert schema["EncodedBytes"]["properties"]["data"]["format"] == "eip-base64-unpadded"
    assert schema["EnvironmentChangedNotification"]["properties"]["observed_generation"]["maximum"] == 2**64 - 1
    assert schema["EIPError"]["properties"]["code"]["minimum"] == -(2**31)

    openrpc = json.loads(OPENRPC_PATH.read_text(encoding="utf-8"))
    shell_exec = next(method for method in openrpc["methods"] if method["name"] == "shell.exec")
    assert [item["name"] for item in shell_exec["params"]] == ["context", "request"]
    assert all(item["required"] is True for item in shell_exec["params"])


def test_verify_generated_does_not_modify_checked_tree(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    before = snapshot(repository)

    verify_generated(candidate, repository)

    assert snapshot(repository) == before


def test_verify_generated_reports_changed_generated_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    (repository / PYTHON_PATH / "models.py").write_text(f"{GENERATED_HEADER}VALUE = 2\n", encoding="utf-8")

    with pytest.raises(SystemExit, match=r"generated file differs:.*models\.py"):
        verify_generated(candidate, repository)


def test_verify_generated_reports_untracked_marked_file(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    repository = tmp_path / "repository"
    write_generated_tree(candidate)
    write_generated_tree(repository)
    extra = repository / PYTHON_PATH / "obsolete.py"
    extra.write_text(f"{GENERATED_HEADER}VALUE = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit, match=r"untracked generated file:.*obsolete\.py"):
        verify_generated(candidate, repository)

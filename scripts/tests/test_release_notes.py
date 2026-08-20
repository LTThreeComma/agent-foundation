from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIRECTORY = Path(__file__).parents[1]
CREATE_RELEASE = SCRIPTS_DIRECTORY / "create-github-release.py"
sys.path.insert(0, str(SCRIPTS_DIRECTORY))

from release_notes import (  # noqa: E402
    INITIAL_NOTES,
    build_release_command,
    previous_release_tag,
    release_tag,
)


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("foundation", "release/foundation-v1.2.3"),
        ("agent-envd", "release/agent-envd-v1.2.3"),
        ("sdk-python", "release/sdk/python/1.2.3"),
        ("sdk-go", "release/sdk/go/1.2.3"),
        ("sdk-rust", "release/sdk/rust/1.2.3"),
        ("sdk-typescript", "release/sdk/typescript/1.2.3"),
    ],
)
def test_builds_canonical_channel_tag(component: str, expected: str) -> None:
    assert release_tag(component, "1.2.3") == expected


def test_finds_highest_lower_version_in_same_channel() -> None:
    tags = [
        "release/foundation-v1.9.0",
        "release/foundation-v1.10.0",
        "release/foundation-v2.0.0",
        "release/foundation-vnot-a-version",
        "release/agent-envd-v1.99.0",
        "release/sdk/python/1.99.0",
    ]

    assert previous_release_tag("foundation", "2.0.0", tags) == "release/foundation-v1.10.0"


def test_first_channel_release_has_no_previous_tag() -> None:
    tags = [
        "release/foundation-v1.0.0",
        "release/agent-envd-v0.0.0",
        "release/sdk/python/0.0.0",
    ]

    assert previous_release_tag("foundation", "0.0.0", tags) is None


def test_builds_generated_notes_command_for_later_release() -> None:
    command = build_release_command(
        component="sdk-python",
        version="1.2.3",
        repository="converge-ai-labs/agent-foundation",
        title="Foundation SDK for Python 1.2.3",
        assets=["dist/package.whl", "dist/package.tar.gz"],
        previous_tag="release/sdk/python/1.2.2",
    )

    assert command == [
        "gh",
        "release",
        "create",
        "release/sdk/python/1.2.3",
        "dist/package.whl",
        "dist/package.tar.gz",
        "--repo",
        "converge-ai-labs/agent-foundation",
        "--verify-tag",
        "--title",
        "Foundation SDK for Python 1.2.3",
        "--generate-notes",
        "--notes-start-tag",
        "release/sdk/python/1.2.2",
    ]


def test_builds_initial_release_command_without_cross_channel_notes() -> None:
    command = build_release_command(
        component="agent-envd",
        version="0.0.0",
        repository="converge-ai-labs/agent-foundation",
        title="agent-envd 0.0.0",
        assets=[],
        previous_tag=None,
    )

    assert "--generate-notes" not in command
    assert "--notes-start-tag" not in command
    assert command[-2:] == ["--notes", INITIAL_NOTES["agent-envd"]]


def test_cli_scopes_generated_notes_to_previous_channel_tag(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "README.md").write_text("release test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "test release"], cwd=tmp_path, check=True)
    for tag in (
        "release/sdk/python/0.0.0",
        "release/sdk/python/0.0.1",
        "release/sdk/rust/9.9.9",
    ):
        subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)

    asset = tmp_path / "package.whl"
    asset.write_bytes(b"wheel")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    arguments_file = tmp_path / "gh-arguments"
    fake_gh = fake_bin / "gh"
    fake_gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$GH_ARGUMENTS_FILE"\n', encoding="utf-8")
    fake_gh.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "GH_ARGUMENTS_FILE": str(arguments_file),
            "GITHUB_REF_NAME": "release/sdk/python/0.0.1",
            "GITHUB_REPOSITORY": "converge-ai-labs/agent-foundation",
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CREATE_RELEASE),
            "sdk-python",
            "0.0.1",
            "Foundation SDK for Python 0.0.1",
            str(asset),
        ],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "release/sdk/python/0.0.0...release/sdk/python/0.0.1" in result.stdout
    arguments = arguments_file.read_text(encoding="utf-8").splitlines()
    assert arguments[-2:] == ["--notes-start-tag", "release/sdk/python/0.0.0"]
    assert "release/sdk/rust/9.9.9" not in arguments

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from release_version import COMPONENTS, validate_version_syntax

TAG_PREFIXES = {
    "foundation": "release/foundation-v",
    "agent-envd": "release/agent-envd-v",
    "sdk-python": "release/sdk/python/",
    "sdk-go": "release/sdk/go/",
    "sdk-rust": "release/sdk/rust/",
    "sdk-typescript": "release/sdk/typescript/",
}
INITIAL_NOTES = {
    "foundation": "Initial release for Agent Foundation.",
    "agent-envd": "Initial release for agent-envd.",
    "sdk-python": "Initial release for the Foundation SDK for Python.",
    "sdk-go": "Initial release for the Foundation SDK for Go.",
    "sdk-rust": "Initial release for the Foundation SDK for Rust.",
    "sdk-typescript": "Initial release for the Foundation SDK for TypeScript.",
}
RELEASE_NOTES_DIRECTORY = Path(".github/release-notes")


class ReleaseNotesError(ValueError):
    pass


def _tag_prefix(component: str) -> str:
    if component not in COMPONENTS:
        raise ReleaseNotesError(f"Unknown release component: {component}")
    return TAG_PREFIXES[component]


def release_tag(component: str, version: str) -> str:
    validate_version_syntax(version)
    return f"{_tag_prefix(component)}{version}"


def _version_key(version: str) -> tuple[int, int, int]:
    validate_version_syntax(version)
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def previous_release_tag(component: str, version: str, tags: Iterable[str]) -> str | None:
    prefix = _tag_prefix(component)
    current_key = _version_key(version)
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for tag in tags:
        if not tag.startswith(prefix):
            continue
        candidate_version = tag.removeprefix(prefix)
        try:
            candidate_key = _version_key(candidate_version)
        except ValueError:
            continue
        if candidate_key < current_key:
            candidates.append((candidate_key, tag))
    if not candidates:
        return None
    return max(candidates)[1]


def release_notes_path(component: str, version: str) -> Path:
    release_tag(component, version)
    return RELEASE_NOTES_DIRECTORY / component / f"{version}.md"


def read_manual_release_notes(root: Path, component: str, version: str) -> str | None:
    relative_path = release_notes_path(component, version)
    path = root / relative_path
    if not path.exists():
        return None
    if not path.is_file():
        raise ReleaseNotesError(f"Release notes path is not a file: {relative_path}")
    try:
        notes = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise ReleaseNotesError(f"Cannot read release notes from {relative_path}: {error}") from error
    return notes or None


def build_release_command(
    *,
    component: str,
    version: str,
    repository: str,
    title: str,
    assets: Sequence[str],
    previous_tag: str | None,
    manual_notes: str | None = None,
) -> list[str]:
    tag = release_tag(component, version)
    command = [
        "gh",
        "release",
        "create",
        tag,
        *assets,
        "--repo",
        repository,
        "--verify-tag",
        "--title",
        title,
    ]
    if previous_tag is None:
        command.extend(("--notes", manual_notes or INITIAL_NOTES[component]))
    else:
        if manual_notes is not None:
            command.extend(("--notes", manual_notes))
        command.extend(("--generate-notes", "--notes-start-tag", previous_tag))
    return command

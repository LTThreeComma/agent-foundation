from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from release_notes import TAG_PREFIXES, build_release_command, previous_release_tag, release_tag
from release_version import COMPONENTS


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _release_tags(component: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "tag", "--list", f"{TAG_PREFIXES[component]}*"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"Cannot list release tags: {error}") from error
    return result.stdout.splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a GitHub Release with notes scoped to one component release channel."
    )
    parser.add_argument("component", choices=COMPONENTS)
    parser.add_argument("version")
    parser.add_argument("title")
    parser.add_argument("assets", nargs="*")
    args = parser.parse_args()

    try:
        repository = _required_environment("GITHUB_REPOSITORY")
        current_ref = _required_environment("GITHUB_REF_NAME")
        expected_tag = release_tag(args.component, args.version)
        if current_ref != expected_tag:
            raise ValueError(f"Expected release tag {expected_tag}, got {current_ref}")

        tags = _release_tags(args.component)
        if expected_tag not in tags:
            raise ValueError(f"Release tag is missing from the checkout: {expected_tag}")
        previous_tag = previous_release_tag(args.component, args.version, tags)

        missing_assets = [asset for asset in args.assets if not Path(asset).is_file()]
        if missing_assets:
            details = ", ".join(missing_assets)
            raise ValueError(f"Release assets do not exist: {details}")

        command = build_release_command(
            component=args.component,
            version=args.version,
            repository=repository,
            title=args.title,
            assets=args.assets,
            previous_tag=previous_tag,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    if previous_tag is None:
        print(f"Creating {expected_tag} as the first release in its channel")
    else:
        print(f"Generating release notes for {previous_tag}...{expected_tag}")
    try:
        result = subprocess.run(command, check=False)
    except OSError as error:
        raise SystemExit(f"Cannot run gh: {error}") from error
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()

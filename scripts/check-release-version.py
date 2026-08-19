from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

FOUNDATION_MANIFESTS = (
    Path("pyproject.toml"),
    Path("packages/agent-harness/pyproject.toml"),
    Path("packages/logging/pyproject.toml"),
    Path("packages/foundation-service/pyproject.toml"),
)


def project_version(path: Path) -> str:
    with path.open("rb") as file:
        project = tomllib.load(file).get("project")
    if not isinstance(project, dict):
        raise SystemExit(f"Missing project.version in {path}")
    version = project.get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Missing project.version in {path}")
    return version


def agent_envd_version() -> str:
    path = Path("Cargo.toml")
    with path.open("rb") as file:
        workspace = tomllib.load(file).get("workspace")
    if not isinstance(workspace, dict):
        raise SystemExit(f"Missing workspace.package.version in {path}")
    package = workspace.get("package")
    if not isinstance(package, dict):
        raise SystemExit(f"Missing workspace.package.version in {path}")
    version = package.get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Missing workspace.package.version in {path}")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Check release manifest versions.")
    parser.add_argument("component", choices=("foundation", "agent-envd"))
    parser.add_argument("version")
    args = parser.parse_args()

    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version) is None:
        raise SystemExit(f"Release version must use X.Y.Z syntax: {args.version}")

    versions = (
        {path: project_version(path) for path in FOUNDATION_MANIFESTS}
        if args.component == "foundation"
        else {Path("Cargo.toml"): agent_envd_version()}
    )
    mismatches = {path: version for path, version in versions.items() if version != args.version}
    if mismatches:
        details = "\n".join(f"- {path}: {version}" for path, version in mismatches.items())
        raise SystemExit(f"Expected {args.component} version {args.version}:\n{details}")

    print(f"Validated {args.component} version {args.version}")


if __name__ == "__main__":
    main()

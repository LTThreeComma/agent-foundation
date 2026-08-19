from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

FOUNDATION_MANIFESTS = (
    Path("pyproject.toml"),
    Path("packages/agent-harness/pyproject.toml"),
    Path("packages/logging/pyproject.toml"),
    Path("packages/foundation-service/pyproject.toml"),
)
SDK_PYTHON_MANIFEST = Path("sdk/python/pyproject.toml")
SDK_RUST_MANIFEST = Path("sdk/rust/Cargo.toml")
SDK_TYPESCRIPT_MANIFESTS = (
    Path("sdk/typescript/package.json"),
    Path("sdk/typescript/package-lock.json"),
)
RELEASE_VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def project_version(path: Path) -> str:
    with path.open("rb") as file:
        project = tomllib.load(file).get("project")
    if not isinstance(project, dict):
        raise SystemExit(f"Missing project.version in {path}")
    version = project.get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Missing project.version in {path}")
    return version


def cargo_package_version(path: Path) -> str:
    with path.open("rb") as file:
        package = tomllib.load(file).get("package")
    if not isinstance(package, dict):
        raise SystemExit(f"Missing package.version in {path}")
    version = package.get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Missing package.version in {path}")
    return version


def npm_package_version(path: Path) -> str:
    with path.open(encoding="utf-8") as file:
        manifest = json.load(file)
    if not isinstance(manifest, dict):
        raise SystemExit(f"Missing version in {path}")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Missing version in {path}")
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
    parser.add_argument(
        "component",
        choices=("foundation", "agent-envd", "sdk-python", "sdk-go", "sdk-rust", "sdk-typescript"),
    )
    parser.add_argument("version")
    args = parser.parse_args()

    if RELEASE_VERSION_PATTERN.fullmatch(args.version) is None:
        raise SystemExit(f"Release version must use X.Y.Z syntax: {args.version}")

    if args.component == "foundation":
        versions = {path: project_version(path) for path in FOUNDATION_MANIFESTS}
    elif args.component == "agent-envd":
        versions = {Path("Cargo.toml"): agent_envd_version()}
    elif args.component == "sdk-python":
        versions = {SDK_PYTHON_MANIFEST: project_version(SDK_PYTHON_MANIFEST)}
    elif args.component == "sdk-rust":
        versions = {SDK_RUST_MANIFEST: cargo_package_version(SDK_RUST_MANIFEST)}
    elif args.component == "sdk-typescript":
        versions = {path: npm_package_version(path) for path in SDK_TYPESCRIPT_MANIFESTS}
    else:
        versions = {}
    mismatches = {path: version for path, version in versions.items() if version != args.version}
    if mismatches:
        details = "\n".join(f"- {path}: {version}" for path, version in mismatches.items())
        raise SystemExit(f"Expected {args.component} version {args.version}:\n{details}")

    print(f"Validated {args.component} version {args.version}")


if __name__ == "__main__":
    main()

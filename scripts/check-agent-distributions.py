from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

PACKAGES = {
    "converge-agent-harness": "converge_agent_harness",
    "converge-agent-stream-protocol": "converge_agent_stream_protocol",
}


class DistributionError(ValueError):
    pass


def _metadata(path: Path) -> tuple[str, str, list[str]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            matches = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(matches) != 1:
                raise DistributionError(f"Expected one METADATA file in {path}, found {len(matches)}")
            content = archive.read(matches[0])
    else:
        with tarfile.open(path, mode="r:gz") as archive:
            matches = [member for member in archive.getmembers() if Path(member.name).name == "PKG-INFO"]
            if len(matches) != 1:
                raise DistributionError(f"Expected one PKG-INFO file in {path}, found {len(matches)}")
            file = archive.extractfile(matches[0])
            if file is None:
                raise DistributionError(f"Cannot read PKG-INFO from {path}")
            content = file.read()
    message = BytesParser(policy=default).parsebytes(content)
    name = message.get("Name")
    version = message.get("Version")
    requirements = message.get_all("Requires-Dist", [])
    if not isinstance(name, str) or not isinstance(version, str):
        raise DistributionError(f"Missing Name or Version metadata in {path}")
    if not all(isinstance(requirement, str) for requirement in requirements):
        raise DistributionError(f"Invalid Requires-Dist metadata in {path}")
    return name, version, requirements


def _validate_protocol_requirement(
    requirements: list[str],
    version: str,
    path: Path,
    *,
    require_exact_internal_version: bool,
) -> None:
    package_name = "converge-agent-harness"
    package_pattern = re.compile(rf"^{re.escape(package_name)}(?=$|\s|[<>=!~;@\[])")
    matches = [requirement for requirement in requirements if package_pattern.match(requirement)]
    expected = f"{package_name}=={version}" if require_exact_internal_version else package_name
    if matches != [expected]:
        raise DistributionError(f"Expected {expected} in {path} metadata, found {matches}")


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def validate_distributions(dist_dir: Path, *, require_exact_internal_version: bool = False) -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise DistributionError("uv is required to validate Harness distributions")

    wheels: dict[str, Path] = {}
    sdists: dict[str, Path] = {}
    metadata: list[tuple[str, str, list[str], Path]] = []
    for path in (*dist_dir.glob("*.whl"), *dist_dir.glob("*.tar.gz")):
        name, version, requirements = _metadata(path)
        if name not in PACKAGES:
            continue
        artifacts = wheels if path.suffix == ".whl" else sdists
        if name in artifacts:
            raise DistributionError(f"Found more than one {path.suffix} artifact for {name}")
        artifacts[name] = path.resolve()
        metadata.append((name, version, requirements, path))
    missing_wheels = sorted(set(PACKAGES) - set(wheels))
    missing_sdists = sorted(set(PACKAGES) - set(sdists))
    if missing_wheels or missing_sdists:
        raise DistributionError(f"Missing Harness artifacts: wheels={missing_wheels}, sdists={missing_sdists}")
    versions = {version for _, version, _, _ in metadata}
    if len(versions) != 1:
        raise DistributionError(f"Harness distribution versions do not match: {sorted(versions)}")
    version = versions.pop()
    for name, _, requirements, path in metadata:
        if name == "converge-agent-stream-protocol":
            _validate_protocol_requirement(
                requirements,
                version,
                path,
                require_exact_internal_version=require_exact_internal_version,
            )

    with tempfile.TemporaryDirectory(prefix="harness-dist-check-") as directory:
        temporary = Path(directory)
        for distribution, module in PACKAGES.items():
            environment = temporary / distribution
            create = subprocess.run(
                [uv, "venv", "--python", sys.executable, str(environment)],
                check=False,
                capture_output=True,
                text=True,
            )
            if create.returncode != 0:
                raise DistributionError(f"Cannot create smoke environment for {distribution}:\n{create.stderr}")
            python = _venv_python(environment)
            install = subprocess.run(
                [
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--find-links",
                    str(dist_dir.resolve()),
                    str(wheels[distribution]),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if install.returncode != 0:
                raise DistributionError(f"Cannot install {distribution} wheel in isolation:\n{install.stderr}")
            smoke = subprocess.run(
                [
                    str(python),
                    "-c",
                    (
                        f"import {module}; from importlib.metadata import version; "
                        f"assert {module}.__version__ == version('{distribution}') == '{version}'"
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if smoke.returncode != 0:
                raise DistributionError(
                    f"Cannot import installed {distribution} wheel in isolation:\n{smoke.stdout}{smoke.stderr}"
                )
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Install and smoke-test same-version Harness wheels in isolation.")
    parser.add_argument("dist_dir", type=Path)
    parser.add_argument("--require-exact-internal-version", action="store_true")
    args = parser.parse_args()

    try:
        version = validate_distributions(
            args.dist_dir,
            require_exact_internal_version=args.require_exact_internal_version,
        )
    except (DistributionError, OSError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(str(error)) from error
    print(f"Validated isolated Harness distribution installs at version {version}")


if __name__ == "__main__":
    main()

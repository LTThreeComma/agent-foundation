"""Fresh checkout-owned foundation storage and foreground startup."""

import argparse
import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "setup",
            "service-dev",
            "dev",
            "dev-status",
            "status",
            "down",
            "stop",
            "reset",
            "langfuse",
            "env-list",
        ),
    )
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    identity = hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
    project = f"a13n-rewrite-{identity}"
    directory = ROOT / "var" / "service-rewrite"
    config = directory / "local.toml"
    environment = {**os.environ, "A13N_DEV_POSTGRES_PORT": "0"}
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-name",
        project,
        "-f",
        str(ROOT / "dev/service/compose.yaml"),
    ]

    def docker(*arguments: str, capture: bool = False) -> str:
        result = subprocess.run([*compose, *arguments], env=environment, text=True, capture_output=capture, check=True)
        return result.stdout or ""

    if args.command in {"reset", "langfuse", "env-list", "stop"}:
        parser.error(f"{args.command} is not implemented for the new Service foundation")
    if args.command == "down":
        docker("down")
        return
    if args.command in {"status", "dev-status"}:
        if not config.is_file():
            parser.error("Service configuration is missing; run make setup first")
        settings = tomllib.loads(config.read_text())
        database = urlsplit(settings["database"]["url"])
        print(
            json.dumps(
                {
                    "identity": identity,
                    "project": project,
                    "config": str(config),
                    "service": f"http://{settings['server']['host']}:{settings['server']['port']}",
                    "database": {"host": database.hostname, "port": database.port, "name": database.path.lstrip("/")},
                    "postgres": docker("port", "postgres", "5432", capture=True).strip(),
                }
            )
        )
        return
    docker("up", "-d", "--wait", "postgres")
    port = int(docker("port", "postgres", "5432", capture=True).strip().rsplit(":", 1)[1])
    directory.mkdir(parents=True, exist_ok=True)
    # A new Compose project owns this database; no old development volume is reused.
    config.write_text(
        f'[server]\nhost = "127.0.0.1"\nport = {18000 + int(identity[:4], 16) % 20000}\n\n[database]\nurl = "postgresql+psycopg://a13n_service_dev:local-only-password@127.0.0.1:{port}/a13n_service_dev"\n'
    )
    command = ["uv", "run", "--locked", "a13n-service", "--config", str(config)]
    subprocess.run([*command, "migrate"], check=True)
    print(f"Foundation configuration: {config}", flush=True)
    if args.command in {"service-dev", "dev"}:
        subprocess.run([*command, "run", "--role", "control"], check=True)


if __name__ == "__main__":
    main()

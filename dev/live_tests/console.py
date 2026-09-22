"""Serve the real Console with disposable stores, Control, Worker and HTTP model."""

import argparse
import asyncio
import base64
import json
import os
import secrets
import signal
import socket
import sys
from contextlib import ExitStack
from pathlib import Path

import httpx2
from a13n_service.distribution import OSS
from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.migrations.runner import upgrade
from a13n_service.settings import Database
from a13n_service.tenancy.bootstrap import BootstrapInput, bootstrap
from a13n_service.tenancy.tables import GrantRow, OrganizationRow, PasswordRow, PrincipalRow, WorkspaceRow
from argon2 import PasswordHasher
from pydantic import SecretStr
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from dev.fixtures.model import model_process

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "console-fixture-password"


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def initialize(database: Database):
    storage = Storage(database.url.get_secret_value())
    try:
        initialized = await bootstrap(
            storage, BootstrapInput(email="console@example.com", password=SecretStr(PASSWORD))
        )
        viewer_id = new_object_id("usr")
        password_hash = PasswordHasher().hash(PASSWORD)
        async with transaction(storage) as session:
            session.add(
                PrincipalRow(id=viewer_id, kind="user", email="viewer@example.com", name="Viewer", status="active")
            )
            await session.flush()
            session.add(PasswordRow(principal_id=viewer_id, hash=password_hash))
            session.add(
                GrantRow(
                    id=new_object_id("rb"),
                    organization_id=initialized.organization_id,
                    workspace_id=initialized.workspace_id,
                    principal_id=viewer_id,
                    role="viewer",
                    created_by_id=initialized.principal_id,
                )
            )
            other_org = new_object_id("org")
            session.add(OrganizationRow(id=other_org, key="hidden", name="Hidden organization"))
            await session.flush()
            session.add(
                WorkspaceRow(
                    id=new_object_id("ws"), organization_id=other_org, key="ambiguous", name="Hidden workspace"
                )
            )
            own = await session.get(WorkspaceRow, initialized.workspace_id)
            assert own is not None
            own.key = "ambiguous"
        return initialized
    finally:
        await storage.close()


async def serve(directory: Path, database: Database, redis_url: str, model_url: str) -> None:
    initialized = await initialize(database)
    control_port, worker_port, console_port = [available_port() for _ in range(3)]
    environment = {name: value for name, value in os.environ.items() if not name.startswith("A13N_")}
    processes = []
    encryption_key = base64.b64encode(secrets.token_bytes(32)).decode()
    with ExitStack() as logs:
        try:
            for role, port in (("control", control_port), ("worker", worker_port)):
                config = directory / f"{role}.toml"
                config.write_text(f"""[server]
port = {port}
shutdown_timeout = 5
[database]
url = {json.dumps(database.url.get_secret_value())}
auto_migrate = false
[redis]
url = {json.dumps(redis_url)}
[objects]
root = {json.dumps(str(directory / "objects"))}
[encryption]
active_key_id = "fixture"
[encryption.keys]
fixture = {json.dumps(encryption_key)}
[providers]
private_cidrs = ["127.0.0.0/8"]
http_origins = [{json.dumps(model_url.removesuffix("/v1"))}]
[worker]
slots = 2
lease_seconds = 5
scan_seconds = 0.2
authority_seconds = 0.2
[control]
scan_seconds = 0.2
""")
                config.chmod(0o600)
                log = logs.enter_context((directory / f"{role}.log").open("wb"))
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "from a13n_service.cli import main; main()",
                    "--config",
                    str(config),
                    "run",
                    "--role",
                    role,
                    cwd=ROOT,
                    env=environment,
                    start_new_session=True,
                    stdout=log,
                    stderr=log,
                )
                processes.append(process)
                async with httpx2.AsyncClient(trust_env=False, timeout=2) as client, asyncio.timeout(30):
                    while True:
                        if process.returncode is not None:
                            raise RuntimeError(f"{role} failed; see {role}.log")
                        try:
                            response = await client.get(f"http://127.0.0.1:{port}/readyz")
                            if response.status_code == 200:
                                break
                        except httpx2.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
            console_log = logs.enter_context((directory / "console.log").open("wb"))
            processes.append(
                await asyncio.create_subprocess_exec(
                    "pnpm",
                    "--filter",
                    "a13n-console",
                    "exec",
                    "vite",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(console_port),
                    cwd=ROOT / "frontend",
                    env={**environment, "A13N_CONSOLE_SERVICE_URL": f"http://127.0.0.1:{control_port}"},
                    start_new_session=True,
                    stdout=console_log,
                    stderr=console_log,
                )
            )
            url = f"http://localhost:{console_port}"
            async with httpx2.AsyncClient(trust_env=False, timeout=2) as client, asyncio.timeout(30):
                while True:
                    try:
                        if (await client.get(url)).status_code == 200:
                            break
                    except httpx2.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            state = {
                "url": url,
                "model_url": model_url,
                "workspace_id": initialized.workspace_id,
                "organization_id": initialized.organization_id,
                "redis_url": redis_url,
                "pid": os.getpid(),
                "processes": [process.pid for process in processes],
            }
            (directory / "state.json").write_text(json.dumps(state, indent=2) + "\n")
            print(json.dumps(state), flush=True)
            stopped = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stopped.set)
            await stopped.wait()
        finally:
            for process in processes:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGTERM)
            for process in processes:
                try:
                    async with asyncio.timeout(10):
                        await process.wait()
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New evidence directory owned by this fixture")
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    with (
        PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres,
        RedisContainer("redis:8-alpine") as redis,
        model_process(port=0) as model_url,
    ):
        database = Database(url=SecretStr(postgres.get_connection_url()))
        upgrade(database, OSS)
        asyncio.run(
            serve(
                directory,
                database,
                f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0",
                model_url,
            )
        )


if __name__ == "__main__":
    main()

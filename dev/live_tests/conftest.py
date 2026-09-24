"""Fixtures of the live journeys: session-wide stores and a fresh Service stack for every journey."""

import re
import signal
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest
from redis import Redis as SyncRedis
from redis.asyncio import Redis
from sqlalchemy import Engine, create_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from dev.fixtures.process import fixture_process

from .api import Workspace, expect
from .scripted import ScriptedModel
from .stack import (
    EMAIL,
    PASSWORD,
    ServiceProcess,
    Stores,
    cloned_database,
    database_url,
    prepare_template,
    trust,
    wait_ready,
    write_config,
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-all",
        action="store_true",
        help="Fail instead of skipping journeys whose external dependency is unavailable (CI)",
    )
    parser.addoption(
        "--hosted",
        action="store_true",
        help="Also run the journeys on hosted sandbox vendors, which need their accounts and create billable sandboxes",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "hosted(type): a journey on a hosted sandbox vendor, run only when asked for")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Hosted journeys run only when asked for: with `--hosted`, a `-m` expression naming the `hosted` marker, or a
    `-k` expression naming the journey's vendor type; any other selection leaves them out."""
    if config.getoption("--hosted") or re.search(r"\bhosted\b", config.getoption("markexpr") or ""):
        return
    named = set(re.findall(r"\w+", config.getoption("keyword") or ""))
    left_out = [item for item in items if (marker := item.get_closest_marker("hosted")) and marker.args[0] not in named]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if item not in left_out]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if report.when == "call":
        item.stash[FAILED] = report.failed
    return report


FAILED = pytest.StashKey[bool]()


@pytest.fixture(scope="session")
def stores(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stores]:
    with (
        PostgresContainer("postgres:17-alpine", driver="psycopg") as postgres,
        RedisContainer("redis:8-alpine") as redis,
    ):
        redis_url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
        yield prepare_template(tmp_path_factory.mktemp("stores"), postgres.get_connection_url(), redis_url)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Stack:
    """One journey's Service: its processes, its workspace client, the scripted model and read-only stores."""

    api: Workspace
    model: ScriptedModel
    control: ServiceProcess
    workers: list[ServiceProcess]
    redis: Redis
    database: Engine
    directory: Path
    stores: Stores

    def client(self, **headers: str) -> httpx2.AsyncClient:
        """Another HTTPS client of Control, such as one authenticated by an API key."""
        return httpx2.AsyncClient(
            base_url=self.control.url, verify=trust(self.stores), trust_env=False, timeout=10, headers=headers
        )

    @contextmanager
    def only(self, worker: ServiceProcess) -> Iterator[None]:
        """Suspend the other workers, so that runs accepted meanwhile are claimed by `worker`."""
        others = [other for other in self.workers if other is not worker]
        for other in others:
            other.signal(signal.SIGSTOP)
        try:
            yield
        finally:
            for other in others:
                other.signal(signal.SIGCONT)


@pytest.fixture
async def stack(stores: Stores, tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[Stack]:
    SyncRedis.from_url(stores.redis_url).flushall()
    with ExitStack() as cleanup:
        database = cleanup.enter_context(cloned_database(stores))
        config = write_config(tmp_path / "service.toml", stores, database, tmp_path / "objects")
        model_url = cleanup.enter_context(fixture_process("dev.fixtures.scripted_model"))
        control = ServiceProcess("control", config, tmp_path / "control.log")
        workers = [ServiceProcess("worker", config, tmp_path / f"worker-{index}.log") for index in (1, 2)]
        processes = [control, *workers]
        for process in processes:
            cleanup.callback(process.kill)
            process.start()
        context = trust(stores)
        wait_ready(processes, context)
        engine = create_engine(database_url(stores.postgres_url, database))
        cleanup.callback(engine.dispose)
        async with (
            httpx2.AsyncClient(base_url=control.url, verify=context, trust_env=False, timeout=10) as client,
            Redis.from_url(stores.redis_url, decode_responses=True) as redis,
        ):
            login = expect(await client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}), 200)
            client.headers["x-csrf-token"] = login["csrf_token"]
            model = ScriptedModel(model_url)
            try:
                yield Stack(
                    api=Workspace(client, stores.tenant),
                    model=model,
                    control=control,
                    workers=workers,
                    redis=redis,
                    database=engine,
                    directory=tmp_path,
                    stores=stores,
                )
            finally:
                await model.aclose()
                if request.node.stash.get(FAILED, False):
                    for process in processes:
                        print(f"--- {process.role} {process.log.name} ---\n{process.tail()}")

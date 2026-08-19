import asyncio
from typing import cast

import anyio
from converge_foundation_service.db.engine import SessionFactory
from converge_foundation_service.db.session import short_session, transaction


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        await anyio.sleep(0)
        self.rollbacks += 1

    async def close(self) -> None:
        await anyio.sleep(0)
        self.closed = True


class FakeFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


def _factory(session: FakeSession) -> SessionFactory:
    return cast(SessionFactory, FakeFactory(session))


def test_transaction_commits_and_closes() -> None:
    session = FakeSession()

    async def run() -> None:
        async with transaction(_factory(session)) as yielded:
            assert yielded is session

    asyncio.run(run())

    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


def test_transaction_shields_cleanup_from_anyio_cancellation() -> None:
    session = FakeSession()

    async def run() -> None:
        with anyio.CancelScope() as scope:
            async with transaction(_factory(session)):
                scope.cancel()
                await anyio.sleep(0)

    anyio.run(run)

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed


def test_short_session_releases_read_transaction() -> None:
    session = FakeSession()

    async def run() -> None:
        async with short_session(_factory(session)) as yielded:
            assert yielded is session

    asyncio.run(run())

    assert session.commits == 0
    assert session.rollbacks == 1
    assert session.closed

"""Safe transaction snapshots for held-I/O integration evidence."""

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import count

import httpx2
import pytest
from a13n_service.infra.db import Storage, short_session
from sqlalchemy import event, text


async def transactions(storage: Storage) -> list[dict]:
    async with short_session(storage) as session:
        rows = await session.execute(
            text("""
            SELECT pid, state, xact_start, state_change, backend_xid, wait_event_type,
                   md5(query) AS query_hash,
                   CASE WHEN query LIKE '%connection_authorizations%' AND query LIKE '%SKIP LOCKED%' THEN 'oauth_deadlines'
                        WHEN query LIKE '%connection_authorizations%' THEN 'connection_authorizations'
                        WHEN query LIKE '%runs%' THEN 'runs'
                        ELSE split_part(query, ' ', 1) END AS query_kind
            FROM pg_stat_activity
            WHERE datname=current_database() AND pid<>pg_backend_pid() AND xact_start IS NOT NULL
            ORDER BY pid
        """)
        )
        return [dict(row) for row in rows.mappings()]


# The transport remains real. The probe observes connection ownership at its entry,
# including SQL scopes inherited when a parent creates an HTTP task.


@dataclass
class DatabaseHTTPProbe:
    active: dict = field(default_factory=dict)
    requests: list[dict] = field(default_factory=list)
    leases: list[dict] = field(default_factory=list)
    blocked: int = 0
    expected_blocks: int = 0

    def assert_finished_tasks_released(self) -> None:
        leaked = [key for key, (_, task) in self.active.items() if task is not None and task.done()]
        assert not leaked, f"Completed tasks retained SQL leases: {leaked}"


@pytest.fixture
def db_http_probe(public_service, monkeypatch, tmp_path):
    storage = public_service.app.state.storage
    probe = DatabaseHTTPProbe()
    inherited: ContextVar[tuple[int, ...]] = ContextVar("sql_http_leases", default=())
    serial = count(1)

    def checkout(connection, record, proxy):
        lease = next(serial)
        record.info["sql_http_lease"] = lease
        task = asyncio.current_task()
        facts = {
            "lease": lease,
            "pid": connection.info.backend_pid,
            "task": task.get_name() if task else None,
            "origin": type(task.get_coro()).__name__ if task else None,
        }
        if task is not None:
            facts["origin"] = getattr(task.get_coro(), "__qualname__", facts["origin"])
        probe.active[lease] = (facts, task)
        probe.leases.append(facts)
        inherited.set((*(key for key in inherited.get() if key in probe.active), lease))

    def checkin(connection, record):
        lease = record.info.pop("sql_http_lease", None)
        probe.active.pop(lease, None)

    original = httpx2.AsyncHTTPTransport.handle_async_request

    async def send(transport, request):
        held = [key for key in inherited.get() if key in probe.active]
        probe.requests.append(
            {
                "method": request.method,
                "kind": "token"
                if request.url.path.endswith("/token")
                else "mcp"
                if request.url.path.endswith("/mcp")
                else "other",
                "held_leases": held,
                "active_leases": list(probe.active),
            }
        )
        if held:
            probe.blocked += 1
        assert not held, f"SQL lease crossed into real HTTP transport: {held}"
        return await original(transport, request)

    event.listen(storage.engine.sync_engine, "checkout", checkout)
    event.listen(storage.engine.sync_engine, "checkin", checkin)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", send)
    try:
        yield probe
        probe.assert_finished_tasks_released()
        assert probe.blocked == probe.expected_blocks
    finally:
        event.remove(storage.engine.sync_engine, "checkout", checkout)
        event.remove(storage.engine.sync_engine, "checkin", checkin)
        (tmp_path / "sql-http-ownership.json").write_text(
            json.dumps(
                {
                    "requests": probe.requests,
                    "leases": probe.leases,
                    "remaining": [facts for facts, task in probe.active.values()],
                    "blocked": probe.blocked,
                    "expected_blocks": probe.expected_blocks,
                },
                indent=2,
            )
        )

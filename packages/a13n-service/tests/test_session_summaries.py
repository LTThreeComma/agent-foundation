"""Original Session navigation uses one scoped native preview and durable activity."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.runs import activity
from a13n_service.runs.attempts import claim_run, heartbeat
from a13n_service.runs.session_views import SessionFilters, summary_query
from a13n_service.runs.tables import RunRow, SessionRow, ThreadRow
from sqlalchemy import event, select, text, update
from sqlalchemy.dialects import postgresql

pytestmark = pytest.mark.anyio


async def submit(service, key, *, session_id=None, thread_id=None, message="Hello"):
    body = {
        "kind": "message",
        "agent_id": service.agent_id,
        "payload": {"content": [{"type": "text", "text": message}]},
    }
    if thread_id:
        path = service.workspace_path + f"/threads/{thread_id}/inbox"
    else:
        path = service.workspace_path + "/threads"
        body["session_id"] = session_id
    response = await service.client.post(path, headers={"Idempotency-Key": key}, json=body)
    assert response.status_code in {200, 201}, response.text
    return response.json()


async def summary(service, identity):
    response = await service.client.get(service.workspace_path + f"/sessions/{identity}")
    assert response.status_code == 200, response.text
    assert "etag" not in response.headers
    return response.json()


async def test_selected_run_filters_exact_thread_and_bounded_query_count(public_service):
    service = public_service
    first = await submit(service, "first", message="x" * 700)
    second = await submit(service, "sibling", session_id=first["session_id"], message="Newest")
    interrupted = await service.client.post(service.workspace_path + f"/runs/{second['run']['id']}/interrupt")
    assert interrupted.status_code == 200, interrupted.text
    path = service.workspace_path + "/sessions"
    selected = await summary(service, first["session_id"])
    assert selected["run_count"] == 2
    assert selected["preview"]["run_id"] == second["run"]["id"]
    assert selected["preview"]["input_text"] == "Newest"
    assert selected["preview"]["trigger"] == "input"
    # Filter the selected newest Run, never search history for an older matching Run.
    assert (await service.client.get(path, params={"status": "accepted"})).json()["items"] == []
    exact = await service.client.get(path, params={"q": first["thread_id"], "status": "accepted"})
    assert exact.status_code == 200, exact.text
    row = exact.json()["items"][0]
    assert row["selected_thread_id"] == first["thread_id"] and row["run_count"] == 2
    assert row["preview"]["input_text"] == "x" * 512
    statements = []

    def observed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(service.app.state.storage.engine.sync_engine, "before_cursor_execute", observed)
    try:
        await service.client.get(path, params={"limit": 1})
        one = len(statements)
        statements.clear()
        await service.client.get(path, params={"limit": 50})
        assert len(statements) == one
        assert sum("JOIN LATERAL" in statement for statement in statements) == 1
    finally:
        event.remove(service.app.state.storage.engine.sync_engine, "before_cursor_execute", observed)


async def test_activity_stamp_raw_sql_rollback_and_metadata_version(public_service):
    service = public_service
    created = await submit(service, "activity")
    identity, storage = created["session_id"], service.app.state.storage
    before = await summary(service, identity)
    async with transaction(storage) as session:
        resource = await session.get(SessionRow, identity)
        old, version = resource.updated_at, resource.version
        await session.execute(
            update(SessionRow).where(SessionRow.id == identity).values(updated_at=old - timedelta(days=1), version=999)
        )
    unchanged = await summary(service, identity)
    assert unchanged["updated_at"] == before["updated_at"] and unchanged["version"] == version
    async with transaction(storage) as session:
        await session.execute(
            update(SessionRow).where(SessionRow.id == identity).values(labels={"name": "Changed"}, version=999)
        )
    changed = await summary(service, identity)
    assert changed["version"] == version + 1 and changed["updated_at"] > before["updated_at"]
    async with transaction(storage) as session:
        await session.execute(update(SessionRow).where(SessionRow.id == identity).values(version=777))
    assert await summary(service, identity) == changed
    with pytest.raises(RuntimeError, match="rollback"):
        async with transaction(storage) as session:
            await activity.touch(session, service.initialized.workspace_id, [identity])
            raise RuntimeError("rollback")
    assert await summary(service, identity) == changed
    async with transaction(storage) as session:
        await activity.touch(session, service.initialized.workspace_id, [identity])
    touched = await summary(service, identity)
    assert touched["version"] == changed["version"] and touched["updated_at"] > changed["updated_at"]


async def test_lifecycle_queue_replay_and_heartbeat_activity(public_service):
    service = public_service
    first = await submit(service, "source")
    identity = first["session_id"]
    before = await summary(service, identity)
    claim = await claim_run(service.app.state.storage, worker_id="activity", worker_build="test", lease_seconds=60)
    running = await summary(service, identity)
    assert running["updated_at"] > before["updated_at"] and running["version"] == before["version"]
    await heartbeat(service.app.state.storage, claim, lease_seconds=60)
    assert await summary(service, identity) == running
    await submit(service, "queued", thread_id=first["thread_id"], message="Queued")
    queued = await summary(service, identity)
    assert queued["updated_at"] > running["updated_at"]
    await submit(service, "queued", thread_id=first["thread_id"], message="Queued")
    assert await summary(service, identity) == queued


async def test_concurrent_child_fk_checks_do_not_deadlock_activity(public_service):
    service = public_service
    first = await submit(service, "source")
    ready = [asyncio.Event(), asyncio.Event()]
    observed = []

    async def insert(index):
        async with transaction(service.app.state.storage) as session:
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            session.add(
                ThreadRow(
                    id=new_object_id("thrd"),
                    organization_id=service.initialized.organization_id,
                    workspace_id=service.initialized.workspace_id,
                    session_id=first["session_id"],
                    origin="new",
                    labels={},
                )
            )
            await session.flush()  # Both transactions hold Session FK KEY SHARE before either activity lock.
            ready[index].set()
            await ready[1 - index].wait()
            await activity.touch(session, service.initialized.workspace_id, [first["session_id"]])
            observed.append(
                await session.scalar(select(SessionRow.updated_at).where(SessionRow.id == first["session_id"]))
            )

    async with asyncio.timeout(12):
        await asyncio.gather(insert(0), insert(1))
    assert observed[1] >= observed[0]
    result = await summary(service, first["session_id"])
    assert result["run_count"] == 1


async def test_tied_activity_cursor_filter_normalization_and_invalid_bounds(public_service):
    service = public_service
    created = [await submit(service, f"page-{index}") for index in range(3)]
    # A future activity-only stamp gives all rows an exact tie without bypassing the trigger.
    tied = datetime.now(UTC) + timedelta(days=1)
    async with transaction(service.app.state.storage) as session:
        await session.execute(update(SessionRow).values(updated_at=tied))
    path = service.workspace_path + "/sessions"
    params = [("limit", "1"), ("status", "running"), ("status", "accepted"), ("status", "accepted")]
    first = await service.client.get(path, params=params)
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    second = await service.client.get(
        path, params=[("limit", "2"), ("status", "accepted"), ("status", "running"), ("cursor", cursor)]
    )
    assert second.status_code == 200, second.text
    found = first.json()["items"] + second.json()["items"]
    assert [item["id"] for item in found] == sorted((item["session_id"] for item in created), reverse=True)
    assert second.json()["next_cursor"] is None
    for change in ({"status": "accepted"}, {"trigger": "input"}, {"agent_id": service.agent_id}):
        response = await service.client.get(path, params={"cursor": cursor, **change})
        assert response.status_code == 400, response.text
    for invalid in (
        {"updated_after": "2026-01-01T00:00:00"},
        {"updated_after": tied.isoformat(), "updated_before": tied.isoformat()},
        {"trigger": "message"},
    ):
        assert (await service.client.get(path, params=invalid)).status_code == 400
    bounded = await service.client.get(
        path,
        params={"updated_after": tied.isoformat(), "updated_before": (tied + timedelta(seconds=1)).isoformat()},
    )
    assert len(bounded.json()["items"]) == 3
    excluded = await service.client.get(path, params={"updated_before": tied.isoformat()})
    assert excluded.json()["items"] == []


async def test_empty_exact_thread_retention_and_other_resource_stamps(public_service):
    service = public_service
    created = await submit(service, "source")
    identity = new_object_id("thrd")
    async with transaction(service.app.state.storage) as session:
        session.add(
            ThreadRow(
                id=identity,
                organization_id=service.initialized.organization_id,
                workspace_id=service.initialized.workspace_id,
                session_id=created["session_id"],
                origin="new",
                labels={},
            )
        )
    path = service.workspace_path + "/sessions"
    response = await service.client.get(path, params={"q": "  " + identity + "  "})
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["selected_thread_id"] == identity and row["preview"] is None and row["run_count"] == 1
    assert (await service.client.get(path, params={"q": identity, "status": "accepted"})).json()["items"] == []
    assert (await service.client.get(path + "/" + identity)).status_code == 404
    before = await summary(service, created["session_id"])
    async with transaction(service.app.state.storage) as session:
        # Fixture-only simulation of an empty retained log. The online immutable-event
        # trigger correctly forbids DELETE; this does not exercise a retention worker.
        await session.execute(text("TRUNCATE events"))
        for model, resource_id in ((ThreadRow, identity), (RunRow, created["run"]["id"])):
            resource = await session.get(model, resource_id)
            old_version, old_time = resource.version, resource.updated_at
            await session.execute(update(model).where(model.id == resource_id).values(version=777))
            await session.refresh(resource)
            assert resource.version == old_version + 1 and resource.updated_at > old_time
    assert await summary(service, created["session_id"]) == before


async def test_navigation_plan_uses_scoped_indexes_with_substantial_history(public_service, tmp_path):
    service = public_service
    seed = await submit(service, "plan-source")
    response = await service.client.post(service.workspace_path + f"/runs/{seed['run']['id']}/interrupt")
    assert response.status_code == 200, response.text
    async with transaction(service.app.state.storage) as session:
        # Populate realistic history volume in SQL, preserving real schema constraints.
        # Every Session has one Thread and twenty cancelled Runs with distinct sources.
        for table, source_id, series, replacement in (
            (
                "sessions",
                seed["session_id"],
                "generate_series(CAST(:start AS integer), CAST(:end AS integer)) s",
                "jsonb_build_object('id', 'sess_plan_' || s)",
            ),
            (
                "threads",
                seed["thread_id"],
                "generate_series(CAST(:start AS integer), CAST(:end AS integer)) s",
                "jsonb_build_object('id', 'thrd_plan_' || s, 'session_id', 'sess_plan_' || s, "
                "'current_run_id', NULL, 'head_run_id', NULL, 'last_run_id', NULL)",
            ),
            (
                "inbox_entries",
                seed["entry"]["id"],
                "generate_series(CAST(:start AS integer), CAST(:end AS integer)) s CROSS JOIN generate_series(1, 20) r",
                "jsonb_build_object('id', 'in_plan_' || s || '_' || r, 'thread_id', 'thrd_plan_' || s, "
                "'position', r, 'request_key', NULL, 'request_digest', NULL, 'request_kind', NULL, "
                "'request_target', NULL, 'assigned_run_id', NULL, 'status', 'pending', "
                "'incorporated_checkpoint_seq', NULL, 'finished_at', NULL)",
            ),
            (
                "runs",
                seed["run"]["id"],
                "generate_series(CAST(:start AS integer), CAST(:end AS integer)) s CROSS JOIN generate_series(1, 20) r",
                "jsonb_build_object('id', 'run_plan_' || s || '_' || r, 'thread_id', 'thrd_plan_' || s, "
                "'session_id', 'sess_plan_' || s, 'source_entry_id', 'in_plan_' || s || '_' || r)",
            ),
        ):
            for start in range(1, 401, 40):
                await session.execute(
                    text(
                        f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, "
                        f"to_jsonb(template) || {replacement})).* FROM {table} template "
                        f"CROSS JOIN {series} WHERE template.id = :source_id"
                    ),
                    {"source_id": source_id, "start": start, "end": start + 39},
                )
        for table in ("sessions", "threads", "runs", "inbox_entries"):
            await session.execute(text(f"ANALYZE {table}"))
        query, activity_order = summary_query(service.initialized.workspace_id, SessionFilters())
        query = query.order_by(activity_order.desc(), SessionRow.id.desc()).limit(31)
        sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        plan = await session.scalar(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))
        (tmp_path / "session-plan.json").write_text(json.dumps(plan, indent=2))

        def nodes(node):
            yield node
            for child in node.get("Plans", []):
                yield from nodes(child)

        scans = list(nodes(plan[0]["Plan"]))
        indexes = {node.get("Index Name") for node in scans}
        assert "ix_sessions_workspace_activity" in indexes, json.dumps(plan)
        assert "ix_runs_workspace_session_created" in indexes, json.dumps(plan)
        run_scans = [node for node in scans if node.get("Relation Name") == "runs"]
        assert run_scans and all(node["Node Type"] != "Seq Scan" for node in run_scans)
        assert plan[0]["Plan"]["Actual Rows"] == 31
        print(json.dumps({"sessions": 401, "runs": 8001, "plan": plan}))

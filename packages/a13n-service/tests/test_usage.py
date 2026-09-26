"""USD usage projections, immutable revisions and actual delegation scopes."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness.providers.usage import ProviderUsage, UsageMeasure
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord, ProviderUsageRecord
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.tables import UsageRecordRow
from a13n_service.runs.usage import UsageReport, persist
from sqlalchemy import select

pytestmark = pytest.mark.anyio


async def _summary(service, run_id, scope="self"):
    response = await service.client.get(f"{service.workspace}/usage", params={"run_id": run_id, "scope": scope})
    assert response.status_code == 200, response.text
    return response.json()


async def test_latest_versions_exact_usd_and_unknown_provider_charges(service, scripted_model, runs_kit):
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    async with transaction(service.runtime.storage) as session:
        row = (await session.scalars(select(UsageRecordRow).where(UsageRecordRow.run_id == run_id))).one()
    original = ModelUsageRecord.model_validate(row.record)
    revised = original.model_copy(
        update={
            "revision": 2,
            "request_usage": BoundedRequestUsage(
                input_tokens=100,
                cache_read_tokens=70,
                output_tokens=20,
                input_audio_tokens=5,
                audio_seconds="1.25",
                cost="0.1",
            ),
        }
    )
    report = UsageReport(revised, row.model_id, row.price_snapshot)

    def receipt(identity, cost):
        return UsageReport(
            ProviderUsageRecord(
                record_id=identity,
                run_id=original.run_id,
                ordinal=1,
                agent_instance_id=original.agent_instance_id,
                source="search",
                usage=ProviderUsage(
                    usage_id=identity,
                    provider="search",
                    product="web",
                    timestamp=datetime.now(UTC),
                    measures=(UsageMeasure(unit="requests", quantity=1),),
                    cost=cost,
                ),
            )
        )

    receipts = [receipt("known", "0.2"), receipt("unknown", None)]
    await asyncio.gather(
        *(persist(service.runtime.storage, run_id, row.run_attempt_id, [report, *receipts]) for _ in range(3))
    )
    await persist(
        service.runtime.storage, run_id, row.run_attempt_id, [UsageReport(original, row.model_id, row.price_snapshot)]
    )
    result = await _summary(service, run_id)
    assert result["requests"] == 1 and result["input_tokens"] == 100
    assert result["cache_read_tokens"] == 70 and result["input_audio_tokens"] == 5
    assert Decimal(result["audio_seconds"]) == Decimal("1.25")
    assert Decimal(result["cost"]) == Decimal("0.3")
    assert result["provider_receipts"] == 2 and result["unknown_cost_records"] == 1
    assert result["providers"] == [
        {"provider": "search", "product": "web", "receipts": 2, "cost": "0.2", "unknown_cost_records": 1}
    ]
    with pytest.raises(ServiceError, match="Conflicting"):
        await persist(
            service.runtime.storage,
            run_id,
            row.run_attempt_id,
            [
                UsageReport(
                    revised.model_copy(update={"request_usage": BoundedRequestUsage(cost="9")}),
                    row.model_id,
                    row.price_snapshot,
                )
            ],
        )
    assert (await _summary(service, run_id))["cost"] == result["cost"]
    # A first delivery containing multiple versions must not change the fact's owner.
    first = original.model_copy(update={"record_id": "batch-version"})
    moved = first.model_copy(update={"revision": 2, "source": "another-owner"})
    with pytest.raises(ServiceError, match="attribution"):
        await persist(
            service.runtime.storage,
            run_id,
            row.run_attempt_id,
            [UsageReport(item, row.model_id, row.price_snapshot) for item in (first, moved)],
        )
    assert (await _summary(service, run_id))["requests"] == 1


async def test_tree_follows_delegation_and_grows_without_changing_parent_snapshot(service, scripted_model, runs_kit):
    await runs_kit.pause_sweeps(service)
    agent = await runs_kit.delegating(service, scripted_model, "async")
    scripted_model.call(
        "delegate", {"subagent_name": "helper", "prompt": "compute"}, call_id="call_d", to="Role: coordinator"
    )
    scripted_model.say("Delegated", to="Role: coordinator")
    started = await runs_kit.start_thread(service, agent, "delegate")
    parent = started["run"]["id"]
    await (await runs_kit.attempt(service))
    sealed = (await runs_kit.get_run(service, parent))["usage_at_seal"]
    assert (await _summary(service, parent))["requests"] == 2
    tree = await _summary(service, parent, "tree")
    assert tree["requests"] == 2 and tree["active_runs"] == 1
    scripted_model.say("42", to="Role: worker")
    await (await runs_kit.attempt(service))
    tree = await _summary(service, parent, "tree")
    assert tree["requests"] == 3 and tree["active_runs"] == 0
    assert (await _summary(service, parent))["requests"] == 2
    assert (await runs_kit.get_run(service, parent))["usage_at_seal"] == sealed
    # A fork has the same historical parent Run but is not a delegation child.
    fork = await service.client.post(
        f"{service.workspace}/runs/{parent}/fork", json=runs_kit.message(agent, "fork"), headers=runs_kit.fresh_key()
    )
    assert fork.status_code == 201, fork.text
    scripted_model.say("Fork answer", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    assert (await _summary(service, parent, "tree"))["requests"] == 3
    later = await runs_kit.submit(service, started["thread"]["id"], runs_kit.message(agent, "later"))
    assert later.status_code == 201, later.text
    scripted_model.say("Later answer", to="Role: coordinator")
    await (await runs_kit.attempt(service))
    assert (await _summary(service, parent, "tree"))["requests"] == 3
    invalid = await service.client.get(f"{service.workspace}/usage", params={"scope": "tree"})
    assert invalid.status_code == 400


async def test_reporter_failure_does_not_schedule_another_model_attempt(service, scripted_model, runs_kit, monkeypatch):
    from a13n_service.runs.usage import DatabaseUsageReporter

    async def unavailable(self, records):
        raise ServiceError("unavailable", "Usage store unavailable")

    monkeypatch.setattr(DatabaseUsageReporter, "report", unavailable)
    agent = await runs_kit.create_agent(service, scripted_model)
    run_id = (await runs_kit.start_thread(service, agent, "hi"))["run"]["id"]
    scripted_model.say("Done")
    await (await runs_kit.attempt(service))
    result = await runs_kit.get_run(service, run_id)
    assert result["status"] == "failed" and result["failure"]["code"] == "usage_report_failed"
    assert result["attempts"] == 1
    await scripted_model.request()
    assert scripted_model.requests.empty()

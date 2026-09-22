"""One real Harness attempt, with awaited durability and fresh paid-call authority."""

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    HarnessBuilder,
    HarnessEvent,
    HarnessExtensionEvent,
    HarnessState,
    RunBindings,
)
from a13n_harness.capabilities.context import CompactionCapability, CompactionPolicy
from a13n_harness.events import UsageReportPayload
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.pricing import ModelPricingEntry, PricingCatalog
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_harness.providers.model import ModelProviderDefinition
from a13n_harness.usage import ModelUsageRecord
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import TextContent
from pydantic_ai.usage import UsageLimits
from redis.asyncio import Redis

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.resources.models.runtime import open_model
from a13n_service.runs import attempts, inputs, seal, selection
from a13n_service.runs.harness import CheckpointCapability, entry_input
from a13n_service.runs.policy import AdmissionPolicy, CallCheck
from a13n_service.runs.publisher import Publisher
from a13n_service.runs.schemas import AttemptClaim, MessagePayload
from a13n_service.runs.streams import AttemptStream, Bounds, ObservationWriter
from a13n_service.settings import Settings


def native_inputs(run_id: str, entries: tuple[tuple[str, MessagePayload], ...]) -> list[TextContent]:
    return [
        content
        for entry_id, payload in entries
        for part in payload.content
        for content in entry_input(run_id, entry_id, part.text)
    ]


async def execute(
    storage: Storage,
    objects: LocalObjects,
    claim: AttemptClaim,
    *,
    config: Settings,
    redis: Redis,
    catalog: ProviderCatalog[ModelProviderDefinition],
    keys: KeyRing,
    endpoint_policy: EndpointPolicy,
    admission: AdmissionPolicy | None,
) -> None:
    selected, model = await selection.load(storage, claim)
    check = CallCheck(storage, claim, model, admission)
    publisher = Publisher(
        storage,
        objects,
        claim,
        selected.agent,
        selected.options,
        max_bytes=config.objects.max_bytes,
        max_events=config.worker.max_events,
        timeout=config.objects.timeout,
    )
    try:
        previous = await selection.initial_state(objects, claim, selected, timeout=config.objects.timeout)
        await publisher.initialize(previous)
        checkpoint = publisher.checkpoint
        assert checkpoint is not None
        if checkpoint.candidate == "completed":
            await publisher.close()
            _, state_ref, display_ref = publisher.selected()
            await seal.completed(storage, objects, claim, state_ref, display_ref, timeout=config.objects.timeout)
            return
        if checkpoint.candidate is not None:
            raise ServiceError("conflict", "Checkpoint outcome cannot be executed by this worker")

        observer = ObservationWriter(
            AttemptStream(
                redis,
                claim.run_id,
                claim.number,
                Bounds(
                    count=config.worker.stream_count,
                    bytes=config.worker.stream_bytes,
                    entry_bytes=config.worker.stream_entry_bytes,
                    ttl=config.worker.stream_ttl,
                    timeout=config.redis.timeout,
                ),
            ),
            publisher,
            flush_seconds=config.worker.display_flush_seconds,
        )
        await observer.reset()

        async def publish(state: HarnessState, receipts: tuple[str, ...], context: AgentContext) -> None:
            await publisher.publish(state, receipts, context)
            assigned = await inputs.assign_steers(
                storage, claim, max_count=config.control.inbox_count, max_bytes=config.control.inbox_bytes
            )
            if assigned:
                await stream.steer(native_inputs(claim.run_id, assigned))

        capability = CheckpointCapability(claim.run_id, publish, receipts=checkpoint.receipts)
        capabilities: list[AbstractCapability[AgentContext]] = [capability]
        if selected.agent.config.compaction_trigger_tokens is not None:
            capabilities.append(
                CompactionCapability(CompactionPolicy(trigger_tokens=selected.agent.config.compaction_trigger_tokens))
            )
        price = ModelPricingEntry.model_validate(model.pricing) if model.pricing is not None else None
        pricing = PricingCatalog({price.key: price} if price is not None else {})
        settings: dict[str, int | float] = {}
        for name in ("max_tokens", "temperature", "top_p"):
            value = getattr(model.config, name)
            if value is not None:
                settings[name] = value
        offered = native_inputs(claim.run_id, await inputs.assigned_inputs(storage, claim))
        bindings = RunBindings(
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-service", subject=selected.authority.principal_id),
                agent_instance_id=claim.attempt_id,
                host_refs={"run_id": claim.run_id, "thread_id": claim.thread_id, "workspace_id": claim.workspace_id},
            ),
            model_call_check=check,
        )
        async with open_model(
            model,
            organization_id=claim.organization_id,
            catalog=catalog,
            keys=keys,
            policy=endpoint_policy,
            timeout=60,
            max_bytes=config.objects.max_bytes,
        ) as native:
            executable = HarnessBuilder().build(
                AgentDefinition(
                    agent=AgentSpec(instructions=selected.agent.config.instructions, model_settings=settings),
                    output_type=str,
                    model=native,
                    capabilities=tuple(capabilities),
                ),
                pricing_catalog=pricing,
            )
            async with executable.stream(
                offered or None,
                previous_state=checkpoint.state,
                tool_recovery="declared",
                bindings=bindings,
                usage_limits=UsageLimits(request_limit=None),
            ) as stream:
                await attempts.start(storage, claim, harness_run_id=stream.run_id)
                async for item in stream:
                    if (
                        isinstance(item, HarnessEvent)
                        and isinstance(item.event, HarnessExtensionEvent)
                        and isinstance(item.event.payload, dict)
                        and item.event.payload.get("type") == "usage_report"
                    ):
                        report = UsageReportPayload.model_validate(item.event.payload)
                        for record in report.records:
                            await check.ingest(ModelUsageRecord.model_validate(record))
                    await observer.append(publisher.observe(item))
                result = stream.result
                if result is None or result.state is None:
                    raise ServiceError("unavailable", "Harness did not return durable final state")
                for record in result.usage_records:
                    if not isinstance(record, ModelUsageRecord):
                        raise ServiceError("conflict", "Attempt reported usage for an unconfigured paid capability")
                    await check.ingest(record)
                await publisher.finalize(result.state, tuple(capability.receipts), output=result.output_or_raise())
        _, state_ref, display_ref = publisher.selected()
        await seal.completed(storage, objects, claim, state_ref, display_ref, timeout=config.objects.timeout)
    finally:
        await publisher.close()

"""Canonical run-local usage facts, summaries and optional Host delivery."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import anyio
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

from a13n_harness._json import dump_json_bytes, is_sensitive_key
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError, RunError
from a13n_harness.identity import AgentInstanceContext
from a13n_harness.money import sum_decimal
from a13n_harness.pricing import MODEL_COST_CAPABILITY_ID, AbstractModelCostCapability
from a13n_harness.providers.usage import ProviderUsage as ProviderUsage
from a13n_harness.providers.usage import UsageMeasure as UsageMeasure

if TYPE_CHECKING:
    from a13n_harness.events import HarnessEventEmitter

USAGE_CAPABILITY_ID = "a13n.usage"
_MAX_RECORDS = 10_000
_MAX_REPORT_BYTES = 56 * 1024
_MAX_REPORT_RECORDS = 64
_MAX_USAGE_DETAILS = 64
_MAX_COUNTER = 2**63 - 1

type CostSource = Literal[
    "catalog",
    "custom",
    "provider_or_genai_prices",
    "unknown",
]
type PricingStatus = Literal[
    "applied",
    "declined",
    "failed",
    "disabled",
    "not_reached",
]
type UsageReportReason = Literal["model_request", "provider", "terminal"]


_LIMIT_FIELDS = (
    "cost_limit",
    "request_limit",
    "tool_calls_limit",
    "input_tokens_limit",
    "output_tokens_limit",
    "total_tokens_limit",
    "per_request_input_tokens_limit",
)


def intersect_usage_limits(*values: UsageLimits | None) -> UsageLimits | None:
    """Return fresh native limits no broader than any supplied ceiling."""
    present = tuple(value for value in values if value is not None)
    if not present:
        return None
    fields: dict[str, Any] = {}
    for name in _LIMIT_FIELDS:
        ceilings = [getattr(value, name) for value in present if getattr(value, name) is not None]
        fields[name] = min(ceilings) if ceilings else None
    fields["count_tokens_before_request"] = any(value.count_tokens_before_request for value in present)
    return UsageLimits(**fields)


class UsageCounters(BaseModel):
    """Safe fixed-shape projection of one Pydantic request usage value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_write_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    input_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    cache_audio_read_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    output_audio_tokens: int = Field(default=0, ge=0, le=_MAX_COUNTER)
    audio_seconds: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class BoundedRequestUsage(UsageCounters):
    """One request's counters and bounded provider detail, with USD cost."""

    details: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_projection(self) -> BoundedRequestUsage:
        if len(self.details) > _MAX_USAGE_DETAILS:
            raise ValueError("request usage has too many detail counters")
        for key, value in self.details.items():
            if not key or len(key) > 128 or "\x00" in key or not 0 <= value <= _MAX_COUNTER:
                raise ValueError("request usage detail is invalid")
        if self.cost is not None and (not self.cost.is_finite() or self.cost < 0):
            raise ValueError("request usage cost is invalid")
        return self

    @classmethod
    def from_request_usage(cls, usage: RequestUsage) -> BoundedRequestUsage:
        """Copy supported counters and omit unsafe provider detail instead of leaking it."""
        details = {
            key: value
            for key, value in sorted(usage.details.items())
            if isinstance(key, str)
            and bool(key)
            and len(key) <= 128
            and "\x00" not in key
            and not is_sensitive_key(key)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_COUNTER
        }
        return cls(
            input_tokens=usage.input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
            input_audio_tokens=usage.input_audio_tokens,
            cache_audio_read_tokens=usage.cache_audio_read_tokens,
            output_audio_tokens=usage.output_audio_tokens,
            audio_seconds=Decimal(str(usage.audio_seconds)),
            details=dict(tuple(details.items())[:_MAX_USAGE_DETAILS]),
            cost=usage.cost,
        )


class ModelUsageRecord(BaseModel):
    """One observed model generation, independent of native response commitment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["model"] = "model"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    response_ordinal: int = Field(ge=0)
    revision: int = Field(default=1, ge=1)
    model_id: str | None = Field(default=None, max_length=1024)
    provider_response_id: str | None = Field(default=None, max_length=1024)
    usage_status: Literal["complete", "partial", "unavailable"] = "complete"
    outcome: Literal["completed", "failed", "cancelled"] = "completed"
    call_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_run_id: str | None = Field(default=None, max_length=256)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    response_state: str = Field(min_length=1, max_length=64)
    model_name: str | None = Field(default=None, max_length=1024)
    provider_name: str | None = Field(default=None, max_length=512)
    response_timestamp: datetime
    request_started_at: datetime
    request_usage: BoundedRequestUsage
    pricing_revision: str | None = Field(default=None, max_length=256)
    pricing_rule_id: str | None = Field(default=None, max_length=128)
    cost_source: CostSource = "unknown"
    pricing_status: PricingStatus = "not_reached"
    source: str = Field(default="agent", min_length=1, max_length=256)
    tool_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=1024)


class ProviderUsageRecord(BaseModel):
    """One deduplicated non-model usage contribution with run and tool attribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["provider"] = "provider"
    record_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=256)
    ordinal: int = Field(ge=0)
    revision: int = Field(default=1, ge=1)
    agent_instance_id: str = Field(min_length=1, max_length=512)
    parent_agent_instance_id: str | None = Field(default=None, max_length=512)
    delegation_id: str | None = Field(default=None, max_length=512)
    source: str = Field(min_length=1, max_length=256)
    tool_id: str | None = Field(default=None, max_length=256)
    tool_call_id: str | None = Field(default=None, max_length=1024)
    usage: ProviderUsage

    @field_validator("source", "tool_id", "tool_call_id")
    @classmethod
    def _validate_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("usage attribution text must not contain NUL")
        return value


type UsageRecord = ModelUsageRecord | ProviderUsageRecord


class RunUsageSummary(BoundedRequestUsage):
    """Known run-local totals. Unknown and partial records remain explicit."""

    requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    provider_receipts: int = Field(default=0, ge=0)
    unknown_cost_records: int = Field(default=0, ge=0)
    incomplete_requests: int = Field(default=0, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_hit_rate(self) -> float | None:
        return self.cache_read_tokens / self.input_tokens if self.input_tokens else None


def summarize_usage(records: Iterable[UsageRecord], *, tool_calls: int = 0) -> RunUsageSummary:
    """Sum each record's latest revision once; costs include model and provider receipts."""
    latest: dict[str, UsageRecord] = {}
    for record in records:
        previous = latest.get(record.record_id)
        if previous is None or record.revision > previous.revision:
            latest[record.record_id] = record
        elif record.revision == previous.revision and record != previous:
            raise RunError("Conflicting usage record revision.", code="usage_record_conflict")
    counters = dict.fromkeys(TOKEN_COUNTERS, 0)
    details: dict[str, int] = {}
    costs: list[Decimal] = []
    seconds: list[Decimal] = []
    requests = receipts = unknown = incomplete = 0
    for record in latest.values():
        if isinstance(record, ModelUsageRecord):
            requests += 1
            usage = record.request_usage
            for name in counters:
                counters[name] += getattr(usage, name)
            for name, count in usage.details.items():
                details[name] = details.get(name, 0) + count
            seconds.append(usage.audio_seconds)
            incomplete += record.usage_status != "complete"
            cost = usage.cost
        else:
            receipts += 1
            cost = record.usage.cost
        if cost is None:
            unknown += 1
        else:
            costs.append(cost)
    return RunUsageSummary(
        **counters,
        details=dict(sorted(details.items())[:_MAX_USAGE_DETAILS]),
        audio_seconds=sum_decimal(seconds),
        requests=requests,
        tool_calls=tool_calls,
        provider_receipts=receipts,
        cost=sum_decimal(costs) if costs else None,
        unknown_cost_records=unknown,
        incomplete_requests=incomplete,
    )


TOKEN_COUNTERS = (
    "input_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "output_tokens",
    "input_audio_tokens",
    "cache_audio_read_tokens",
    "output_audio_tokens",
)


@runtime_checkable
class UsageReporter(Protocol):
    async def report(self, records: tuple[UsageRecord, ...]) -> None:
        """Accept already captured facts; retry the same records, never model execution."""
        ...


class UsageReportError(RunError):
    def __init__(self) -> None:
        super().__init__("Host usage delivery failed.", code="usage_report_failed")


class RunUsageLedger:
    """Latest run-local facts plus pending delivery; no pre-call durable registration."""

    def __init__(
        self,
        *,
        run_id: str,
        instance: AgentInstanceContext,
        events: HarnessEventEmitter | None = None,
        reporter: UsageReporter | None = None,
        limits: UsageLimits | None = None,
        baseline: RunUsage | None = None,
    ) -> None:
        self.run_id = run_id
        self.instance = instance
        self._events = events
        self.reporter = reporter
        self.cost_capability: AbstractModelCostCapability | None = None
        self._records: dict[str, UsageRecord] = {}
        self._pending: list[UsageRecord] = []
        self._model_ordinal = 0
        self._flush_lock = asyncio.Lock()
        self._limits = limits
        self._baseline = deepcopy(baseline) if baseline is not None else RunUsage()
        self._in_flight = 0

    @property
    def records(self) -> tuple[UsageRecord, ...]:
        return tuple(record.model_copy(deep=True) for record in self._records.values() if record.run_id == self.run_id)

    def summary(self, *, tool_calls: int = 0) -> RunUsageSummary:
        return summarize_usage(
            (r for r in self._records.values() if r.run_id == self.run_id),
            tool_calls=max(0, tool_calls - self._baseline.tool_calls),
        )

    def next_model_ordinal(self) -> int:
        ordinal = self._model_ordinal
        self._model_ordinal += 1
        return ordinal

    def append_model(self, record: ModelUsageRecord) -> None:
        self._append(record)

    def _budget(self) -> RunUsage:
        summary = self.summary()
        value = RunUsage(
            **{name: getattr(summary, name) for name in TOKEN_COUNTERS},
            requests=summary.requests + self._in_flight,
            cost=summary.cost,
        )
        value.incr(self._baseline)
        return value

    def reserve(self, *, continuation: bool = False) -> None:
        # No await between the check and reservation: concurrent tasks cannot oversubscribe.
        if not continuation and len(self._records) + self._in_flight >= _MAX_RECORDS:
            raise RunError("Run usage capacity was exceeded.", code="usage_capacity_exceeded")
        if self._limits is not None:
            limits = replace(self._limits, request_limit=None) if continuation else self._limits
            limits.check_before_request(self._budget())
        self._in_flight += 1

    def release(self) -> None:
        self._in_flight -= 1

    def check_limits(self) -> None:
        if self._limits is not None:
            value = self._budget()
            if self._limits.request_limit is not None and value.requests - self._in_flight > self._limits.request_limit:
                raise UsageLimitExceeded("Continuation exceeded the request_limit")
            self._limits.check_tokens(value)
            self._limits.check_cost(value, warn_if_cost_unavailable=False)

    async def _record_provider(
        self, usage: ProviderUsage, *, source: str, tool_id: str | None = None, tool_call_id: str | None = None
    ) -> ProviderUsageRecord:
        receipt = ProviderUsage.model_validate(usage.model_dump())
        record_id = _stable_id("provider", receipt.provider, receipt.product, receipt.usage_id)
        previous = self._records.get(record_id)
        candidate = ProviderUsageRecord(
            record_id=record_id,
            run_id=self.run_id,
            ordinal=previous.ordinal if isinstance(previous, ProviderUsageRecord) else len(self._records),
            agent_instance_id=self.instance.agent_instance_id,
            parent_agent_instance_id=self.instance.parent_agent_instance_id,
            delegation_id=self.instance.delegation_id,
            source=source,
            tool_id=tool_id,
            tool_call_id=tool_call_id,
            usage=receipt,
        )
        self._append(candidate)
        await self._flush(reason="provider", trigger_record_id=record_id)
        self.check_limits()
        return candidate.model_copy(deep=True)

    def _append(self, record: UsageRecord) -> None:
        previous = self._records.get(record.record_id)
        if previous is not None:
            if record.revision < previous.revision:
                return
            if record.revision == previous.revision:
                if record != previous:
                    raise RunError("Conflicting usage record revision.", code="usage_record_conflict")
                return
        elif len(self._records) >= _MAX_RECORDS:
            raise RunError("Run usage capacity was exceeded.", code="usage_capacity_exceeded")
        self._records[record.record_id] = record.model_copy(deep=True)
        self._pending.append(record.model_copy(deep=True))

    async def _flush(self, *, reason: UsageReportReason, trigger_record_id: str | None = None) -> None:
        with anyio.move_on_after(5, shield=True) as cleanup:
            await self._deliver(reason=reason, trigger_record_id=trigger_record_id)
        if cleanup.cancel_called:
            raise UsageReportError()

    async def _deliver(self, *, reason: UsageReportReason, trigger_record_id: str | None) -> None:
        from a13n_harness.events import UsageReportPayload, emit_harness_event

        async with self._flush_lock:
            pending = self._pending[:]
            if not pending:
                return
            if self.reporter is not None:
                try:
                    await self.reporter.report(tuple(record.model_copy(deep=True) for record in pending))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise UsageReportError() from exc
            if self._events is not None:
                chunks = _report_chunks(pending)
                report_id = _stable_id("report", self.run_id, pending[0].record_id, str(pending[0].revision))
                for index, chunk in enumerate(chunks):
                    await emit_harness_event(
                        self._events,
                        kind="usage",
                        payload=UsageReportPayload(
                            report_id=report_id,
                            reason=reason,
                            trigger_record_id=trigger_record_id,
                            chunk_index=index,
                            chunk_count=len(chunks),
                            records=tuple(record.model_dump(mode="json") for record in chunk),
                        ),
                    )
            del self._pending[: len(pending)]


@dataclass(init=False)
class UsageCapability(AbstractCapability[AgentContext]):
    """Install one run-local meter around the public Model boundary."""

    id = USAGE_CAPABILITY_ID

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(USAGE_CAPABILITY_ID)
        if existing is not None:
            return existing
        replacement = _RunUsageCapability(ctx.deps)
        ctx.deps._record_run_capability(USAGE_CAPABILITY_ID, replacement)
        return replacement

    def get_ordering(self) -> CapabilityOrdering:
        from a13n_harness.models.capability import SelfHealingModelCapability

        return CapabilityOrdering(position="innermost", wraps=[SelfHealingModelCapability])


class _RunUsageCapability(UsageCapability):
    def __init__(self, context: AgentContext) -> None:
        self.context = context

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if ctx.deps is not self.context:
            raise DefinitionError("Usage owner cannot cross runs.", code="capability_scope_invalid")
        return self

    async def wrap_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        from a13n_harness.metering import ModelUsageBinding, meter_request

        if ctx.deps is not self.context:
            raise DefinitionError("Usage owner cannot cross runs.", code="capability_scope_invalid")
        cost = ctx.deps._inherited_model_cost or ctx.capabilities.get(MODEL_COST_CAPABILITY_ID)
        if not isinstance(cost, AbstractModelCostCapability):
            raise DefinitionError("Missing model-cost capability.", code="capability_type_mismatch")
        ctx.deps.usage_attribution.cost_capability = cost
        return await meter_request(
            ModelUsageBinding(ctx.deps.usage_attribution, cost, owner=ctx.deps), ctx, request_context, handler
        )


def _stable_id(kind: str, *values: str) -> str:
    digest = hashlib.sha256("\x00".join((kind, *values)).encode("utf-8")).hexdigest()[:24]
    return f"usage-{digest}"


def _report_chunks(records: list[UsageRecord]) -> list[list[UsageRecord]]:
    chunks: list[list[UsageRecord]] = []
    current: list[UsageRecord] = []
    for record in records:
        candidate = [*current, record]
        encoded = dump_json_bytes([item.model_dump(mode="json") for item in candidate])
        if current and (len(candidate) > _MAX_REPORT_RECORDS or len(encoded) > _MAX_REPORT_BYTES):
            chunks.append(current)
            current = [record]
        else:
            current = candidate
        if len(dump_json_bytes([item.model_dump(mode="json") for item in current])) > _MAX_REPORT_BYTES:
            raise RunError("One usage record exceeds the report size bound.", code="usage_record_too_large")
    if current:
        chunks.append(current)
    return chunks


__all__ = [
    "TOKEN_COUNTERS",
    "BoundedRequestUsage",
    "ModelUsageRecord",
    "ProviderUsage",
    "ProviderUsageRecord",
    "RunUsageLedger",
    "RunUsageSummary",
    "UsageCounters",
    "UsageMeasure",
    "UsageRecord",
    "UsageReportError",
    "UsageReporter",
    "intersect_usage_limits",
    "summarize_usage",
]

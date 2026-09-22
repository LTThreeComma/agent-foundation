"""SQL-only admission and paid-call checks under the current worker and resource authority."""

from dataclasses import dataclass
from typing import Protocol

from a13n_harness.model_calls import ModelCall
from a13n_harness.usage import ModelUsageRecord
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.runtime import ResolvedModel
from a13n_service.resources.models.schemas import ModelConfig
from a13n_service.resources.models.tables import ModelProviderRow, ModelRow
from a13n_service.runs import usage
from a13n_service.runs.attempts import lock_authority
from a13n_service.runs.schemas import AttemptClaim
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.authorize import ExecutionAuthority, Principal, Scope, authorize
from a13n_service.tenancy.grants import principal_for


@dataclass(frozen=True)
class AcceptedIntent:
    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    principal_id: str
    agent_revision_id: str
    model_id: str


@dataclass(frozen=True)
class CallContext:
    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    root_run_id: str
    call_id: str
    model_id: str
    provider_id: str
    source: str
    price_snapshot: dict | None


class AdmissionPolicy(Protocol):
    async def accept(self, session: AsyncSession, intent: AcceptedIntent) -> None: ...
    async def proceed(self, session: AsyncSession, call: CallContext) -> None: ...


async def authorize_execution(session: AsyncSession, run: RunRow) -> Principal:
    if run.cancel_requested_at is not None:
        raise ServiceError("conflict", "Run cancellation was requested", {"reason": "cancelled"})
    authority = ExecutionAuthority.model_validate(run.authority)
    principal = await principal_for(session, run.principal_id, confinement=Scope(run.organization_id, run.workspace_id))
    authorize(principal, Scope(run.organization_id, run.workspace_id), "run", authority=authority)
    return principal


async def resolve_model(session: AsyncSession, run: RunRow, model_id: str, principal: Principal) -> ResolvedModel:
    authority = ExecutionAuthority.model_validate(run.authority)
    model = await session.get(ModelRow, model_id)
    if model is None or not model.enabled:
        raise ServiceError("disabled", "Execution model is unavailable")
    provider = await session.get(ModelProviderRow, model.provider_id)
    if provider is None or not provider.enabled:
        raise ServiceError("disabled", "Execution provider is unavailable")
    authorize(principal, Scope(model.organization_id, model.workspace_id), "run", authority=authority)
    authorize(principal, Scope(provider.organization_id, provider.workspace_id), "run", authority=authority)
    return ResolvedModel(
        id=model.id,
        version=model.version,
        provider_id=provider.id,
        provider_version=provider.version,
        provider_type=provider.type,
        config=ModelConfig.model_validate(model.config),
        provider_config=dict(provider.config),
        credential=provider.credential,
        pricing=model.pricing,
    )


class CallCheck:
    def __init__(self, storage: Storage, claim: AttemptClaim, model: ResolvedModel, policy: AdmissionPolicy | None):
        self.storage, self.claim, self.model, self.policy = storage, claim, model, policy
        self.calls: dict[str, CallContext] = {}

    async def _current_model(self, session: AsyncSession, run: RunRow) -> ResolvedModel:
        principal = await authorize_execution(session, run)
        current = await resolve_model(session, run, self.model.id, principal)
        if current.version != self.model.version or current.provider_version != self.model.provider_version:
            raise ServiceError("disabled", "Execution model configuration changed")
        return current

    async def refresh(self) -> None:
        async with transaction(self.storage) as session:
            run, _, _ = await lock_authority(session, self.claim)
            await self._current_model(session, run)

    async def check(self, call: ModelCall) -> None:
        if len(self.calls) >= 10000 or call.call_id in self.calls:
            raise ServiceError("rate_limited", "Model call capacity reached")
        if call.model_name != self.model.config.model_name or call.provider_name != self.model.provider_type:
            raise ServiceError("forbidden", "Model call differs from its authorized selection")
        async with transaction(self.storage) as session:
            run, attempt, _ = await lock_authority(session, self.claim)
            if call.harness_run_id != attempt.harness_run_id:
                raise ServiceError("forbidden", "Model call belongs to another Harness run")
            current = await self._current_model(session, run)
            recorded = await usage.totals(session, run.id)
            if run.max_usage is not None and recorded["requests"] >= run.max_usage["requests"]:
                raise ServiceError("rate_limited", "Run request limit reached")
            context = CallContext(
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                session_id=run.session_id,
                thread_id=run.thread_id,
                run_id=run.id,
                run_attempt_id=attempt.id,
                root_run_id=run.id,
                call_id=call.call_id,
                model_id=current.id,
                provider_id=current.provider_id,
                source=call.source,
                price_snapshot=current.pricing,
            )
            if self.policy is not None:
                await self.policy.proceed(session, context)
        self.calls[call.call_id] = context

    async def ingest(self, record: ModelUsageRecord) -> None:
        context = self.calls.get(record.call_id) if record.call_id is not None else None
        if context is None:
            raise ServiceError("conflict", "Model usage has no proven authorized call", {"record_id": record.record_id})
        await usage.ingest(
            self.storage, self.claim, record, model_id=context.model_id, price_snapshot=context.price_snapshot
        )

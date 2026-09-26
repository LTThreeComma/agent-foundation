"""The check before every paid call of an attempt: cancellation, the lease, the run's request limit and admission.

Model calls and connection tool calls pass it before they are sent. The Harness reports a refused call as an
error without the reason, so the check keeps what the refusal means: the outcome that ends the run whatever
error the refusal surfaced as, or a lease that missed its renewal, which ends the attempt without an outcome.
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import NoReturn

from a13n_harness.model_calls import ModelCall
from a13n_harness.usage import ModelUsageRecord, UsageRecord

from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.tools import ToolDispatch
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.runs.admission import CallContext
from a13n_service.runs.attempts import AttemptControl
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import Outcome
from a13n_service.runs.usage import price_snapshot


class Refused(Exception):
    pass


class CallCheck:
    """`used` counts the run's model requests so far, including those earlier attempts recorded. `models` are
    the models of the agent graph by ID. Pending requests reserve slots; received model facts replace those
    slots, with repeated revisions of a background generation counted only once."""

    def __init__(
        self,
        runtime: Runtime,
        control: AttemptControl,
        context: CallContext,
        *,
        models: Mapping[str, ResolvedModel],
        used: int,
        limit: int | None,
    ):
        self.runtime, self.control, self.context, self.models = runtime, control, context, models
        self._baseline, self.limit = used, limit
        self._pending: set[str] = set()
        self._recorded: set[str] = set()
        self.refusal: Outcome | None = None
        self.lease_missed = False

    @property
    def used(self) -> int:
        return self._baseline + len(self._pending) + len(self._recorded)

    def recorded(self, records: tuple[UsageRecord, ...], owned: frozenset[str]) -> None:
        for record in records:
            if isinstance(record, ModelUsageRecord):
                if record.call_id is not None:
                    self._pending.discard(record.call_id)
                if record.record_id in owned:
                    self._recorded.add(record.record_id)

    async def check(self, call: ModelCall) -> None:
        self._continuing()
        if call.continuation_of is None and self.limit is not None and self.used >= self.limit:
            self.refuse(Outcome.failed("usage_limit_exceeded", f"The run used its {self.limit} model requests"))
        if call.continuation_of is None:
            self._pending.add(call.call_id)
        try:
            # Every model of the graph is selected by its ID; a call that names none of them cannot be admitted.
            model = self.models.get(call.model_id or "")
            if model is None:
                self.refuse(Outcome.failed("model_call_unknown", "A model call named no model of the agent"))
            await self._admit(
                replace(
                    self.context,
                    call_id=call.call_id,
                    source=call.source,
                    provider_id=model.provider.id,
                    model_id=model.id,
                    price_snapshot=price_snapshot(model),
                )
            )
        except BaseException:
            self._pending.discard(call.call_id)
            raise

    async def tool_call(
        self,
        *,
        call_id: str,
        source: str,
        tool_name: str,
        provider_id: str | None,
        connection_id: str | None = None,
    ) -> None:
        """A paid tool call (a connection or web provider tool); its provider prices it, not a model request."""
        self._continuing()
        await self._admit(
            replace(
                self.context,
                call_id=call_id,
                source=source,
                provider_id=provider_id,
                model_id=None,
                connection_id=connection_id,
                tool_name=tool_name,
                price_snapshot=None,
            )
        )

    async def connection_call(self, call: ToolDispatch) -> None:
        await self.tool_call(
            call_id=call.tool_call_id,
            source="connection",
            tool_name=call.tool_name,
            provider_id=call.provider_id,
            connection_id=call.connection_id,
        )

    def refuse(self, outcome: Outcome) -> NoReturn:
        self.refusal = outcome
        raise Refused(outcome.failure.message if outcome.failure else outcome.status)

    def _continuing(self) -> None:
        if self.control.stopped.is_set():
            self.refuse(self.control.outcome)
        if self.control.renewal_missed():
            self.lease_missed = True
            raise Refused("The attempt's lease missed its renewal")

    async def _admit(self, context: CallContext) -> None:
        if self.runtime.admission is None:
            return
        try:
            async with transaction(self.runtime.storage) as session:
                await self.runtime.admission.proceed(session, context)
        except ServiceError as error:
            if error.code == "unavailable":
                raise
            self.refuse(Outcome.refused(error))

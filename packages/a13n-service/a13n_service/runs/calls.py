"""The check before every paid call of an attempt: cancellation, the lease, the run's request limit and admission.

Model calls and connection tool calls pass it before they are sent. The Harness reports a refused call as an
error without the reason, so the check keeps what the refusal means: the outcome that ends the run whatever
error the refusal surfaced as, or a lease that missed its renewal, which ends the attempt without an outcome.
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import NoReturn

from a13n_harness.model_calls import ModelCall

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
    """`used` counts the run's model requests so far, including those earlier attempts recorded. `served` maps
    each upstream model to the resolved model serving it; it is filled when the attempt opens its models."""

    def __init__(
        self,
        runtime: Runtime,
        control: AttemptControl,
        context: CallContext,
        *,
        served: Mapping[tuple[str, str], ResolvedModel],
        used: int,
        limit: int | None,
    ):
        self.runtime, self.control, self.context, self.served = runtime, control, context, served
        self.used, self.limit = used, limit
        self.refusal: Outcome | None = None
        self.lease_missed = False

    async def check(self, call: ModelCall) -> None:
        self._continuing()
        if self.limit is not None and self.used >= self.limit:
            self.refuse(Outcome.failed("usage_limit_exceeded", f"The run used its {self.limit} model requests"))
        self.used += 1
        model = self.served[(call.provider_name, call.model_name)]
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

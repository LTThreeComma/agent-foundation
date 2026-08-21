"""Code-first Agent construction and the canonical Harness run stream."""

from __future__ import annotations

import asyncio
import inspect
import typing
from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import reduce
from operator import or_
from typing import Any, cast, get_args, get_origin, get_type_hints
from uuid import uuid4

from pydantic import ConfigDict, PydanticSchemaGenerationError, TypeAdapter, ValidationError
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunEvents
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import AgentRunError, RunCancelled, UsageLimitExceeded, UserError
from pydantic_ai.messages import AgentStreamEvent, ModelMessage
from pydantic_ai.models import KnownModelName, Model
from pydantic_ai.output import NativeOutput, OutputSpec, PromptedOutput, TextOutput, ToolOutput
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.tools import DeferredToolRequests, Tool, ToolFuncEither
from pydantic_ai.toolsets import AgentToolset
from pydantic_ai.usage import RunUsage, UsageLimits

from converge_agent_harness.context import EMPTY_SUBAGENTS, AgentContext, RunBindings, SubagentCollection
from converge_agent_harness.errors import (
    DefinitionError,
    HarnessError,
    PluginError,
    RunCleanupError,
    RunError,
    StateError,
)
from converge_agent_harness.events import HarnessEvent, HarnessRunResultEvent, HarnessStreamItem
from converge_agent_harness.input import (
    RunInputFactory,
    RunInputValue,
    RunPreparationContext,
    SemanticRunInput,
    normalize_input,
)
from converge_agent_harness.plugins import (
    AbstractHarnessPlugin,
    BoundPluginContext,
    PluginRunExchange,
    PluginRunNext,
    PluginRunResponse,
    bind_agent_plugins,
    bind_run_plugins,
)
from converge_agent_harness.result import HarnessRunResult, SafeFailure
from converge_agent_harness.state import AgentContextState, HarnessState

_AGENT_EVENT_ADAPTER = TypeAdapter(AgentStreamEvent)


@dataclass(frozen=True, slots=True)
class AgentDefinition[OutputT]:
    """Immutable code-first inputs for one process-local executable Agent."""

    agent: AgentSpec
    output_type: OutputSpec[OutputT]
    definition_id: str = field(default_factory=lambda: str(uuid4()))
    model: Model | KnownModelName | str | None = None
    tools: tuple[Tool[AgentContext] | ToolFuncEither[AgentContext, ...], ...] = ()
    toolsets: tuple[AgentToolset[AgentContext], ...] = ()
    capabilities: tuple[AbstractCapability[AgentContext], ...] = ()
    plugins: tuple[AbstractHarnessPlugin, ...] = ()

    def __post_init__(self) -> None:
        if not self.definition_id.strip():
            raise DefinitionError("definition_id must not be blank.", code="definition_id_invalid")
        object.__setattr__(self, "agent", self.agent.model_copy(deep=True))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "toolsets", tuple(self.toolsets))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "plugins", tuple(self.plugins))


class HarnessBuilder:
    """Build executable Agents through one authoritative Agent.from_spec path."""

    def build[BuildOutputT](self, definition: AgentDefinition[BuildOutputT]) -> ExecutableAgent[BuildOutputT]:
        """Validate code-first composition and construct a reusable executable."""
        plugins, plugin_capabilities = bind_agent_plugins(definition.plugins)
        capabilities = (*definition.capabilities, *plugin_capabilities)
        try:
            agent = Agent.from_spec(
                definition.agent.model_copy(deep=True),
                deps_type=AgentContext,
                model=definition.model,
                output_type=definition.output_type,
                tools=definition.tools,
                toolsets=definition.toolsets,
                capabilities=capabilities,
            )
        except Exception as exc:
            if isinstance(exc, HarnessError):
                raise
            raise DefinitionError(
                "Pydantic AI Agent construction failed.",
                code="agent_build_failed",
                details={"definition_id": definition.definition_id},
            ) from exc
        return ExecutableAgent(
            definition=definition,
            agent=cast(Agent[AgentContext, BuildOutputT], agent),
            output_adapter=_build_output_adapter(agent.output_type),
            plugins=plugins,
            subagents=EMPTY_SUBAGENTS,
        )

    def build_code[BuildOutputT](
        self,
        agent: AgentSpec,
        *,
        output_type: OutputSpec[BuildOutputT],
        definition_id: str | None = None,
        model: Model | KnownModelName | str | None = None,
        tools: Sequence[Tool[AgentContext] | ToolFuncEither[AgentContext, ...]] = (),
        toolsets: Sequence[AgentToolset[AgentContext]] = (),
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        plugins: Sequence[AbstractHarnessPlugin] = (),
    ) -> ExecutableAgent[BuildOutputT]:
        """Convenience constructor retaining the same AgentDefinition build path."""
        return self.build(
            AgentDefinition(
                agent=agent,
                output_type=output_type,
                definition_id=definition_id or str(uuid4()),
                model=model,
                tools=tuple(tools),
                toolsets=tuple(toolsets),
                capabilities=tuple(capabilities),
                plugins=tuple(plugins),
            )
        )


class ExecutableAgent[OutputT]:
    """Reusable code-built Agent that creates one fresh context per invocation."""

    def __init__(
        self,
        *,
        definition: AgentDefinition[OutputT],
        agent: Agent[AgentContext, OutputT],
        output_adapter: TypeAdapter[Any],
        plugins: tuple[AbstractHarnessPlugin, ...],
        subagents: SubagentCollection,
    ) -> None:
        self.definition = definition
        self.subagents = subagents
        self._agent = agent
        self._output_adapter = output_adapter
        self._plugins = plugins
        self._closed = False

    async def run(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunResult[OutputT]:
        """Consume the canonical stream and return its sole terminal result."""
        async with self.stream(
            input,
            input_factory=input_factory,
            bindings=bindings,
            previous_state=previous_state,
            usage=usage,
            usage_limits=usage_limits,
        ) as stream:
            async for item in stream:
                if isinstance(item, HarnessRunResultEvent):
                    return item.result
        raise RunError("The run ended without a terminal result.", code="run_result_missing")

    def stream(
        self,
        input: RunInputValue | None = None,
        *,
        input_factory: RunInputFactory | None = None,
        bindings: RunBindings,
        previous_state: HarnessState | None = None,
        usage: RunUsage | None = None,
        usage_limits: UsageLimits | None = None,
    ) -> HarnessRunStream[OutputT]:
        """Create a lazy, single-entry canonical Harness stream."""
        if self._closed:
            raise RunError("The executable is closed.", code="executable_closed")
        if input is not None and input_factory is not None:
            raise RunError(
                "input and input_factory are mutually exclusive.",
                code="input_source_conflict",
            )
        return HarnessRunStream(
            executable=self,
            input=input,
            input_factory=input_factory,
            bindings=bindings,
            previous_state=previous_state,
            usage=usage,
            usage_limits=usage_limits,
        )

    async def __aenter__(self) -> ExecutableAgent[OutputT]:
        if self._closed:
            raise RunError("The executable is closed.", code="executable_closed")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Idempotently prevent future runs; per-run resources own their cleanup."""
        self._closed = True


class HarnessRunStream[OutputT](AsyncIterator[HarnessStreamItem[OutputT]]):
    """One lazy Pydantic AgentRunEvents stream plus Harness middleware and teardown."""

    def __init__(
        self,
        *,
        executable: ExecutableAgent[OutputT],
        input: RunInputValue | None,
        input_factory: RunInputFactory | None,
        bindings: RunBindings,
        previous_state: HarnessState | None,
        usage: RunUsage | None,
        usage_limits: UsageLimits | None,
    ) -> None:
        self.run_id = str(uuid4())
        self._executable = executable
        self._input = input
        self._input_factory = input_factory
        self._bindings = bindings
        self._previous_state = previous_state.model_copy(deep=True) if previous_state is not None else HarnessState()
        self._usage = usage if usage is not None else RunUsage()
        self._usage_limits = usage_limits
        self._stack = AsyncExitStack()
        self._context: AgentContext | None = None
        self._response: PluginRunResponse[OutputT] | None = None
        self._responses: list[tuple[int, PluginRunResponse[OutputT]]] = []
        self._response_ids: set[int] = set()
        self._pydantic_events: AgentRunEvents[OutputT] | None = None
        self._latest_messages: tuple[ModelMessage, ...] = self._previous_state.message_history
        self._new_message_index = len(self._latest_messages)
        self._source_sequence = 0
        self._public_sequence = 0
        self._last_valid_outcome: HarnessRunResult[OutputT] | None = None
        self._result: HarnessRunResult[OutputT] | None = None
        self._entered = False
        self._iterated = False
        self._closed = False
        self._terminal_yielded = False
        self._cancel_requested = False
        self._next_active = False

    @property
    def context(self) -> AgentContext:
        """Return the fresh run context after stream entry."""
        if self._context is None:
            raise RunError("The stream is not entered.", code="run_not_active")
        return self._context

    @property
    def result(self) -> HarnessRunResult[OutputT] | None:
        """Return the terminal result only after its result event was delivered."""
        return self._result if self._terminal_yielded else None

    @property
    def usage(self) -> RunUsage:
        """Return the live Pydantic AI usage accumulator."""
        return self._usage

    async def __aenter__(self) -> HarnessRunStream[OutputT]:
        if self._entered:
            raise RunError("HarnessRunStream cannot be entered more than once.", code="run_stream_reused")
        self._entered = True
        try:
            environment = await self._stack.enter_async_context(
                self._bindings.environment.bind(run_id=self.run_id, instance=self._bindings.instance)
            )
            preparation = RunPreparationContext(
                run_id=self.run_id,
                instance=self._bindings.instance,
                environment=environment,
                metadata=self._bindings.metadata,
            )
            input_value = self._input
            if self._input_factory is not None:
                try:
                    input_value = await self._input_factory(preparation)
                except Exception as exc:
                    raise RunError("Run input factory failed.", code="input_factory_failed") from exc
            semantic_input = normalize_input(input_value)
            plugin_context = BoundPluginContext()
            context = AgentContext(
                run_id=self.run_id,
                instance=self._bindings.instance,
                state=AgentContextState(self._previous_state.agent_context_state),
                environment=environment,
                plugins=plugin_context,
                subagents=self._executable.subagents,
                metadata=self._bindings.metadata,
            )
            self._context = context
            run_plugins = await bind_run_plugins(self._executable._plugins, context)
            exchange = PluginRunExchange(
                input=semantic_input,
                context=context,
                _state_exporter=self.export_state,
            )
            self._response = self._build_response(run_plugins, 0, exchange)
            return self
        except asyncio.CancelledError as exc:
            await self._close_resources(outcome=None, cancellation=exc)
            raise
        except BaseException:
            await self._close_resources(outcome=None)
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        if not self._closed:
            cancellation = exc_value if isinstance(exc_value, asyncio.CancelledError) else None
            await self._close_resources(outcome=self._last_valid_outcome, cancellation=cancellation)

    def __aiter__(self) -> HarnessRunStream[OutputT]:
        if not self._entered or self._closed:
            raise RunError("The stream is not active.", code="run_not_active")
        if self._iterated:
            raise RunError("HarnessRunStream has exactly one consumer.", code="run_stream_reused")
        self._iterated = True
        return self

    async def __anext__(self) -> HarnessStreamItem[OutputT]:
        if not self._entered or self._closed or self._terminal_yielded:
            raise StopAsyncIteration
        if self._next_active:
            raise RunError(
                "Concurrent iteration of HarnessRunStream is not supported.",
                code="run_stream_concurrent_next",
            )
        self._next_active = True
        try:
            return await self._next_item()
        finally:
            self._next_active = False

    async def _next_item(self) -> HarnessStreamItem[OutputT]:
        assert self._response is not None
        try:
            item = await self._response.__anext__()
            if isinstance(item, HarnessRunResult):
                result = self._validate_result_candidate(item)
                self._last_valid_outcome = result
                await self._close_resources(outcome=result)
                self._result = result
                self._terminal_yielded = True
                return HarnessRunResultEvent(
                    run_id=self.run_id,
                    sequence=self._next_public_sequence(),
                    occurred_at=datetime.now(UTC),
                    result=result,
                )

            if not isinstance(item, HarnessEvent) or item.run_id != self.run_id:
                raise PluginError("Plugin emitted an invalid stream item.", code="plugin_event_invalid")
            try:
                event = _AGENT_EVENT_ADAPTER.validate_python(item.event, strict=True)
            except ValidationError as exc:
                raise PluginError(
                    "Plugin emitted an invalid Pydantic AI event.",
                    code="plugin_event_invalid",
                ) from exc
            return HarnessEvent(
                run_id=self.run_id,
                sequence=self._next_public_sequence(),
                occurred_at=datetime.now(UTC),
                event=event,
            )
        except StopAsyncIteration as exc:
            error = PluginError(
                "Plugin middleware ended without a result candidate.",
                code="plugin_result_missing",
            )
            error.__cause__ = exc
            await self._raise_after_failure(error)
        except asyncio.CancelledError as exc:
            await self._close_resources(outcome=self._last_valid_outcome, cancellation=exc)
            raise
        except RunCleanupError as exc:
            if self._closed:
                raise
            await self._raise_after_failure(exc)
        except BaseException as exc:
            await self._raise_after_failure(exc)
        raise AssertionError("unreachable")

    async def _raise_after_failure(self, failure: BaseException) -> None:
        """Close a failed stream and retain an already validated inner outcome."""
        outcome = self._last_valid_outcome
        try:
            await self._close_resources(outcome=outcome)
        except RunCleanupError as cleanup_error:
            if outcome is None:
                raise cleanup_error from failure
            raise RunCleanupError(
                "Harness run failed after producing an outcome and cleanup also failed.",
                outcome=outcome,
                causes=(failure, *cleanup_error.causes),
            ) from failure
        if outcome is not None:
            raise RunCleanupError(
                "Harness run failed after producing an outcome.",
                outcome=outcome,
                causes=(failure,),
            ) from failure
        raise failure

    def cancel(self) -> None:
        """Request native Pydantic AI cancellation, including before Agent start."""
        if self._terminal_yielded or self._closed:
            return
        self._cancel_requested = True
        if self._pydantic_events is not None:
            self._pydantic_events.cancel()

    async def export_state(self) -> HarnessState:
        """Export the latest complete message and Capability-state boundary."""
        if not self._entered or self._closed or self._context is None:
            raise StateError("The run is not active.", code="run_not_active")
        self._refresh_live_messages()
        return await self._context.export_state(self._latest_messages)

    def _build_response(
        self,
        plugins: tuple[AbstractHarnessPlugin, ...],
        index: int,
        exchange: PluginRunExchange,
    ) -> PluginRunResponse[OutputT]:
        if index == len(plugins):
            return self._register_response(PluginRunResponse(self._agent_items(exchange)), depth=index)
        plugin = plugins[index]
        call_next = PluginRunNext[OutputT](
            lambda next_exchange: self._build_response(plugins, index + 1, next_exchange)
        )
        response = plugin.wrap_run(exchange, call_next)
        if not isinstance(response, PluginRunResponse):
            raise PluginError(
                "Plugin wrap_run must return PluginRunResponse.",
                code="plugin_response_invalid",
                details={"plugin_id": plugin.plugin_id},
            )
        return self._register_response(cast(PluginRunResponse[OutputT], response), depth=index)

    def _register_response(
        self,
        response: PluginRunResponse[OutputT],
        *,
        depth: int,
    ) -> PluginRunResponse[OutputT]:
        response_id = id(response)
        if response_id not in self._response_ids:
            self._response_ids.add(response_id)
            self._responses.append((depth, response))
        return response

    async def _agent_items(
        self,
        exchange: PluginRunExchange,
    ) -> AsyncIterator[HarnessEvent | HarnessRunResult[OutputT]]:
        if exchange.context is not self.context:
            raise PluginError(
                "Plugin middleware replaced the trusted run context.",
                code="plugin_context_replaced",
            )
        if not isinstance(exchange.input, SemanticRunInput):
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid")
        try:
            semantic_input = normalize_input(exchange.input.value)
        except HarnessError as exc:
            raise PluginError("Plugin middleware supplied an invalid input.", code="plugin_input_invalid") from exc

        manager = self._executable._agent.run_stream_events(
            semantic_input.value,
            message_history=self._previous_state.message_history,
            run_id=self.run_id,
            deps=self.context,
            usage=self._usage,
            usage_limits=self._usage_limits,
            capabilities=self._bindings.capabilities,
        )
        try:
            async with manager as events:
                self._pydantic_events = events
                if self._cancel_requested:
                    events.cancel()
                try:
                    async for event in events:
                        self._refresh_live_messages()
                        if isinstance(event, AgentRunResultEvent):
                            result = event.result
                            messages = tuple(result.all_messages())
                            self._latest_messages = messages
                            state = await exchange.context.export_state(messages)
                            if isinstance(result.output, DeferredToolRequests):
                                candidate = HarnessRunResult(
                                    run_id=self.run_id,
                                    status="suspended",
                                    output=None,
                                    deferred=result.output,
                                    suspend_reason="deferred",
                                    state=state,
                                    usage=result.usage,
                                    _messages=messages,
                                    _new_message_index=self._new_message_index,
                                )
                            else:
                                candidate = HarnessRunResult(
                                    run_id=self.run_id,
                                    status="completed",
                                    output=result.output,
                                    state=state,
                                    usage=result.usage,
                                    _messages=messages,
                                    _new_message_index=self._new_message_index,
                                )
                            yield self._record_inner_candidate(candidate)
                            return
                        yield self._adapt_event(cast(AgentStreamEvent, event))
                except RunCancelled as exc:
                    messages = tuple(exc.all_messages())
                    self._latest_messages = messages
                    state = await exchange.context.export_state(messages) if exc.run_id is not None else None
                    yield self._record_inner_candidate(
                        HarnessRunResult(
                            run_id=self.run_id,
                            status="cancelled",
                            output=None,
                            state=state,
                            usage=self._usage if exc.run_id is None else exc.usage,
                            _messages=messages,
                            _new_message_index=min(self._new_message_index, len(messages)),
                        )
                    )
                except UsageLimitExceeded:
                    yield await self._failed_candidate(
                        code="usage_limit_exceeded",
                        message="Pydantic AI usage limit was exceeded.",
                    )
                except AgentRunError:
                    yield await self._failed_candidate(
                        code="agent_run_failed",
                        message="Pydantic AI agent execution failed.",
                    )
        finally:
            self._pydantic_events = None

    async def _failed_candidate(self, *, code: str, message: str) -> HarnessRunResult[OutputT]:
        self._refresh_live_messages()
        state = await self.context.export_state(self._latest_messages)
        return self._record_inner_candidate(
            HarnessRunResult(
                run_id=self.run_id,
                status="failed",
                output=None,
                state=state,
                usage=self._current_usage(),
                failure=SafeFailure(
                    code=code,
                    message=message,
                    retry_hint="dependency_change",
                ),
                _messages=self._latest_messages,
                _new_message_index=min(self._new_message_index, len(self._latest_messages)),
            )
        )

    def _record_inner_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        validated = self._validate_result_candidate(candidate)
        self._last_valid_outcome = validated
        return validated

    def _validate_result_candidate(
        self,
        candidate: HarnessRunResult[OutputT],
    ) -> HarnessRunResult[OutputT]:
        if candidate.run_id != self.run_id:
            raise PluginError(
                "Plugin result run_id does not match the active run.",
                code="plugin_result_run_mismatch",
            )
        try:
            messages = candidate.all_messages()
            new_messages = candidate.new_messages()
            new_message_index = len(messages) - len(new_messages)
            if new_message_index < 0 or messages[new_message_index:] != new_messages:
                raise ValueError("new messages are not a suffix of all messages")
            validated = HarnessRunResult(
                run_id=candidate.run_id,
                status=candidate.status,
                output=candidate.output,
                state=candidate.state,
                usage=candidate.usage,
                failure=candidate.failure,
                suspend_reason=candidate.suspend_reason,
                deferred=candidate.deferred,
                _messages=messages,
                _new_message_index=new_message_index,
            )
            if validated.status == "completed":
                output = validated.output
                if isinstance(output, DeferredToolRequests):
                    raise ValueError("deferred output must suspend the run")
                self._executable._output_adapter.validate_python(output, strict=True)
            return validated
        except (TypeError, ValueError, ValidationError) as exc:
            raise PluginError(
                "Plugin emitted an invalid result candidate.",
                code="plugin_result_invalid",
            ) from exc

    def _adapt_event(self, event: AgentStreamEvent) -> HarnessEvent:
        envelope = HarnessEvent(
            run_id=self.run_id,
            sequence=self._source_sequence,
            occurred_at=datetime.now(UTC),
            event=event,
        )
        self._source_sequence += 1
        return envelope

    def _next_public_sequence(self) -> int:
        sequence = self._public_sequence
        self._public_sequence += 1
        return sequence

    def _refresh_live_messages(self) -> None:
        events = self._pydantic_events
        if events is None:
            return
        try:
            self._latest_messages = tuple(events.all_messages())
        except UserError:
            # Before Pydantic binds the run, imported history remains the latest complete boundary.
            return

    def _current_usage(self) -> RunUsage:
        events = self._pydantic_events
        if events is None:
            return self._usage
        try:
            return events.usage
        except UserError:
            return self._usage

    async def _close_resources(
        self,
        *,
        outcome: HarnessRunResult[Any] | None,
        cancellation: asyncio.CancelledError | None = None,
    ) -> None:
        if self._closed:
            if cancellation is not None:
                raise cancellation
            return

        current_task = asyncio.current_task()
        if cancellation is not None and current_task is not None:
            while current_task.cancelling():
                current_task.uncancel()
        causes: list[BaseException] = []

        async def finish_cleanup(awaitable: Awaitable[None]) -> None:
            nonlocal cancellation
            cleanup_task = asyncio.ensure_future(awaitable)
            while True:
                try:
                    await asyncio.shield(cleanup_task)
                    return
                except asyncio.CancelledError as exc:
                    if current_task is not None and current_task.cancelling():
                        if cancellation is None:
                            cancellation = exc
                        while current_task.cancelling():
                            current_task.uncancel()
                        continue
                    causes.append(exc)
                    return
                except BaseException as exc:
                    causes.append(exc)
                    return

        responses = sorted(self._responses, key=lambda item: item[0], reverse=True)
        for _, response in responses:
            await finish_cleanup(response.aclose())
        await finish_cleanup(self._stack.aclose())
        self._closed = True

        if cancellation is not None:
            for cause in causes:
                cancellation.add_note(f"Harness cleanup also failed: {cause!r}")
            raise cancellation
        if causes:
            raise RunCleanupError(
                "Harness run cleanup failed.",
                outcome=outcome,
                causes=tuple(causes),
            )


def _build_output_adapter(output_spec: OutputSpec[Any]) -> TypeAdapter[Any]:
    """Build a validator for the semantic value returned by an output specification."""
    output_types: list[Any] = []

    def collect(value: Any) -> None:
        if isinstance(value, NativeOutput | PromptedOutput):
            collect(value.outputs)
        elif isinstance(value, ToolOutput):
            collect(value.output)
        elif isinstance(value, TextOutput):
            collect_callable(value.output_function)
        elif isinstance(value, Sequence):
            for item in value:
                collect(item)
        elif get_origin(value) in (typing.Union, type(str | int)):
            for item in get_args(value):
                collect(item)
        elif inspect.isfunction(value) or inspect.ismethod(value):
            collect_callable(value)
        elif value is None:
            output_types.append(type(None))
        else:
            output_types.append(value)

    def collect_callable(function: Any) -> None:
        return_type = get_type_hints(function).get("return", Any)
        collect(return_type)

    collect(output_spec)
    validation_type = reduce(or_, output_types)
    try:
        return TypeAdapter(validation_type)
    except PydanticSchemaGenerationError:
        return TypeAdapter(validation_type, config=ConfigDict(arbitrary_types_allowed=True))

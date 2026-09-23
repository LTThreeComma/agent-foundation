"""Trace export: one OTLP pipeline per executable to the deployment's trace backend, and attempt correlation.

The Service owns the OpenTelemetry SDK and hands the Harness an explicit tracer provider. Every Harness span of
an attempt carries the attempt's correlation as Harness observation metadata; trace queries match the same
attributes, so `correlation_attributes` is the one owner of their names.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Protocol

from a13n_harness import HarnessInstrumentation, HarnessObservationContext, HarnessTraceContent
from a13n_logging import get_logger
from anyio import CancelScope, move_on_after, to_thread
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = get_logger(__name__)

EXPORT_SECONDS = 10
SHUTDOWN_SECONDS = 10

_METADATA = "a13n.observation.metadata."
_SESSION = "a13n.observation.session.id"


class ExportTarget(Protocol):
    """Where the OTLP/HTTP exporter sends spans, and the headers that authorize it."""

    @property
    def otlp_endpoint(self) -> str: ...

    @property
    def otlp_headers(self) -> dict[str, str]: ...


def correlation_attributes(
    organization_id: str,
    workspace_id: str,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    run_attempt_id: str | None = None,
) -> dict[str, str]:
    """The span attributes naming a tenant scope and, when given, a session, thread, run and attempt within it.

    The Harness owns the `run_id` and `thread_id` metadata keys (its own run and thread), hence
    `service_run_id`; the thread is the observation session, by which Langfuse groups a conversation, so the
    Service session is plain metadata.
    """
    attributes = {
        f"{_METADATA}organization_id": organization_id,
        f"{_METADATA}workspace_id": workspace_id,
        f"{_METADATA}session_id": session_id,
        f"{_METADATA}service_run_id": run_id,
        f"{_METADATA}run_attempt_id": run_attempt_id,
        _SESSION: thread_id,
    }
    return {key: value for key, value in attributes.items() if value is not None}


def attempt_observation(
    *, organization_id: str, workspace_id: str, session_id: str, thread_id: str, run_id: str, run_attempt_id: str
) -> HarnessObservationContext:
    """The Harness observation context of one attempt; pass it as `RunBindings.observation`."""
    metadata = correlation_attributes(
        organization_id, workspace_id, session_id=session_id, run_id=run_id, run_attempt_id=run_attempt_id
    )
    return HarnessObservationContext(
        session_id=thread_id, metadata={key.removeprefix(_METADATA): value for key, value in metadata.items()}
    )


@asynccontextmanager
async def open_tracing(
    target: ExportTarget | None, *, content: HarnessTraceContent
) -> AsyncIterator[HarnessInstrumentation | None]:
    """The executable's Harness instrumentation, exporting to `target`; None when tracing is disabled.

    Spans leave in background batches, so a slow or failing backend never delays execution; exit flushes
    what is queued within a bounded time.
    """
    if target is None:
        yield None
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": "a13n-service", "service.version": version("a13n-service")}),
        shutdown_on_exit=False,
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=target.otlp_endpoint, headers=target.otlp_headers, timeout=EXPORT_SECONDS)
        )
    )
    try:
        yield HarnessInstrumentation(tracer_provider=provider, trace_content=content)
    finally:
        with CancelScope(shield=True), move_on_after(SHUTDOWN_SECONDS) as scope:
            try:
                await to_thread.run_sync(provider.shutdown, abandon_on_cancel=True)
            except Exception as error:
                logger.warning("Trace export shutdown failed", extra={"error_type": type(error).__name__})
        if scope.cancel_called:
            logger.warning("Trace export did not flush before shutdown")

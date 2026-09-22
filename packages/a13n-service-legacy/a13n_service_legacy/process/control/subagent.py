"""Control-owned maintenance of durable subagent lifecycles."""

from a13n_service_legacy.environments.websocket.coordination import ConnectionCoordination
from a13n_service_legacy.interactions.inbox import RedisThreadControlSignals
from a13n_service_legacy.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service_legacy.interactions.outcomes import RunOutcomeService
from a13n_service_legacy.process.runtime import SharedRuntime
from a13n_service_legacy.run_stream import RunDisplayStore
from a13n_service_legacy.settings import Settings
from a13n_service_legacy.subagents.cancellation import ChildCancellationReconciler
from a13n_service_legacy.subagents.maintenance import SubagentMaintenance
from a13n_service_legacy.subagents.results import AsyncSubagentResultPublisher
from a13n_service_legacy.subagents.successors import AsyncSubagentSuccessorReconciler


def build_subagent_maintenance(
    settings: Settings, shared: SharedRuntime, display: RunDisplayStore
) -> SubagentMaintenance:
    if shared.memory_behaviors is None:
        raise RuntimeError("Execution memory behavior composition is required")
    sessions = shared.storage.sessions
    signals = RedisThreadControlSignals(shared.storage.redis)
    outcomes = RunOutcomeService(
        sessions, RunPayloadStore(shared.storage.objects), lifecycle=shared.lifecycle, control_signals=signals
    )
    return SubagentMaintenance(
        ChildCancellationReconciler(sessions, outcomes),
        AsyncSubagentResultPublisher(sessions, display, signals=signals),
        AsyncSubagentSuccessorReconciler(
            sessions,
            RunStateStore(shared.storage.objects),
            display,
            bindings=shared.memory_behaviors,
            coordination=ConnectionCoordination(shared.storage.redis),
            lifecycle=shared.lifecycle,
            signals=signals,
        ),
        poll_interval_seconds=settings.subagents.reconcile_poll_interval_seconds,
        batch_limit=settings.control.recovery_batch_limit,
        item_timeout_seconds=settings.control.recovery_item_timeout_seconds,
    )

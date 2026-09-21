"""Control-owned Thread recovery, separate from Worker execution."""

from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.queue_drain import QueueDrain
from a13n_service.process.background import BackgroundTask, periodic_task
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


def build_recovery_tasks(
    settings: Settings,
    shared: SharedRuntime,
    commands: InteractionCommands,
) -> tuple[BackgroundTask, ...]:
    queue = QueueDrain(
        shared.storage.sessions,
        commands.queued,
        batch_limit=settings.control.recovery_batch_limit,
        item_timeout_seconds=settings.control.recovery_item_timeout_seconds,
    )
    return (
        periodic_task(
            "queued_submission_recovery",
            queue.scan,
            interval_seconds=settings.control.recovery_poll_interval_seconds,
            timeout_seconds=(settings.control.recovery_item_timeout_seconds + 1)
            * settings.control.recovery_batch_limit,
        ),
    )

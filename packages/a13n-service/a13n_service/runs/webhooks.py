"""Lifecycle webhooks: every run or attempt transition stages its deliveries in the same transaction."""

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.outbox import enqueue
from a13n_service.resources.subscriptions.tables import SubscriptionRow
from a13n_service.runs.tables import AttemptRow, RunRow

type LifecycleKind = Literal[
    "run.accepted",
    "run.running",
    "run.waiting",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run_attempt.leased",
    "run_attempt.running",
    "run_attempt.succeeded",
    "run_attempt.yielded",
    "run_attempt.failed",
    "run_attempt.cancelled",
]

LIFECYCLE_KINDS: tuple[str, ...] = LifecycleKind.__value__.__args__


def _run(run: RunRow) -> dict[str, JsonValue]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "thread_id": run.thread_id,
        "agent_id": run.agent_id,
        "agent_revision_id": run.agent_revision_id,
        "status": run.status,
        "trigger": run.trigger,
        "wait_reason": run.wait_reason,
        "failure": run.failure,
    }


def _attempt(attempt: AttemptRow) -> dict[str, JsonValue]:
    return {
        "id": attempt.id,
        "number": attempt.number,
        "status": attempt.status,
        "start_reason": attempt.start_reason,
        "yield_reason": attempt.yield_reason,
        "failure": attempt.failure,
    }


async def notify_subscribers(
    session: AsyncSession,
    keys: KeyRing,
    run: RunRow,
    kinds: Sequence[LifecycleKind],
    *,
    at: datetime,
    attempt: AttemptRow | None = None,
    limit: int,
) -> None:
    """The last phase of a transition: one outbox row per matching subscription and lifecycle kind.

    The subscription read here is the selection point; later edits affect only later transitions.
    """
    subscriptions: Sequence[SubscriptionRow] = (
        await session.scalars(
            select(SubscriptionRow)
            .where(
                SubscriptionRow.workspace_id == run.workspace_id,
                SubscriptionRow.enabled,
                or_(*(SubscriptionRow.kinds.contains([kind]) for kind in kinds)),
                or_(
                    ~SubscriptionRow.filter.has_key("agent_id"),
                    SubscriptionRow.filter["agent_id"].astext == run.agent_id,
                ),
                or_(
                    ~SubscriptionRow.filter.has_key("session_id"),
                    SubscriptionRow.filter["session_id"].astext == run.session_id,
                ),
                or_(
                    ~SubscriptionRow.filter.has_key("thread_id"),
                    SubscriptionRow.filter["thread_id"].astext == run.thread_id,
                ),
            )
            .order_by(SubscriptionRow.id)
            .limit(limit)
        )
    ).all()
    for subscription in subscriptions:
        secret = keys.reveal(
            Envelope.model_validate(subscription.signing_secret),
            SecretLocation(subscription.organization_id, "subscriptions", "signing_secret", subscription.id),
        )
        for kind in kinds:
            if kind not in subscription.kinds:
                continue
            # The delivery ID is the outbox row ID, the dedupe identity receivers see, and the secret's AAD.
            delivery_id = new_object_id("obx")
            protected = keys.protect(secret, SecretLocation(run.organization_id, "outbox", "target", delivery_id))
            enqueue(
                session,
                organization_id=run.organization_id,
                workspace_id=run.workspace_id,
                kind="webhook",
                row_id=delivery_id,
                subscription_id=subscription.id,
                target={"url": subscription.url, "signing_secret": protected.model_dump()},
                payload={
                    "id": delivery_id,
                    "type": kind,
                    "occurred_at": at.isoformat(),
                    "workspace_id": run.workspace_id,
                    "run": _run(run),
                    "attempt": _attempt(attempt) if attempt is not None else None,
                },
            )

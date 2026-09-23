"""`maintain_environments`: apply the current idle policies and continue every unfinished operation.

Each pass begins stop and delete operations for managed instances idle past their template's thresholds,
then dispatches a bounded batch of outstanding operations whose claim is free or expired. It revisits failed
operations at its fixed interval with the same operation ID; it never invents a new one.
"""

from functools import partial
from typing import Literal

import anyio
from a13n_logging import get_logger
from sqlalchemy import func, or_, select, text

from a13n_service.infra.audit import record
from a13n_service.infra.db import transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.sweeps import Sweep
from a13n_service.resources.environment_templates.service import idle_past
from a13n_service.runs.environments.lifecycle import advance, begin, in_use, mounted, supports
from a13n_service.runs.environments.tables import OPERATIONS, EnvironmentRow
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import WorkspaceScope

logger = get_logger(__name__)


async def maintain_environments(runtime: Runtime, *, owner: str) -> None:
    """One maintenance pass; `owner` names this process in the claims it takes."""
    batch = runtime.settings.environments.batch
    await _retire_idle(runtime, "deleting", batch)
    await _retire_idle(runtime, "stopping", batch)
    await _dispatch(runtime, owner, batch)


async def _retire_idle(runtime: Runtime, phase: Literal["stopping", "deleting"], batch: int) -> None:
    """Begin idle stops or deletes; active use and mounts are rechecked with fresh statements under each lock."""
    since = func.coalesce(EnvironmentRow.last_used_at, EnvironmentRow.created_at)
    capable = [definition.type for definition in runtime.registry.environments.values() if supports(definition, phase)]
    if phase == "stopping":
        conditions = [
            EnvironmentRow.status == "ready",
            idle_past(EnvironmentRow.template_id, since, "stop_after_seconds"),
        ]
    else:
        conditions = [
            EnvironmentRow.status.in_(("ready", "stopped")),
            idle_past(EnvironmentRow.template_id, since, "delete_after_seconds"),
            ~mounted(EnvironmentRow.id),
        ]
    async with transaction(runtime.storage) as session:
        candidates = (
            await session.scalars(
                select(EnvironmentRow)
                .where(
                    EnvironmentRow.template_id.is_not(None),
                    EnvironmentRow.provider_identity["type"].astext.in_(capable),
                    ~in_use(EnvironmentRow.id),
                    *conditions,
                )
                .order_by(since)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for environment in candidates:
            if await session.scalar(select(in_use(environment.id))) or (
                phase == "deleting" and await session.scalar(select(mounted(environment.id)))
            ):
                continue
            await begin(session, environment, phase)
            record(
                session,
                WorkspaceScope(environment.organization_id, environment.workspace_id),
                actor_id=None,
                action="environment.stop" if phase == "stopping" else "environment.delete",
                target_kind="environment",
                target_id=environment.id,
                details={"reason": "idle"},
            )


async def _dispatch(runtime: Runtime, owner: str, batch: int) -> None:
    async with transaction(runtime.storage) as session:
        due = (
            await session.scalars(
                select(EnvironmentRow.id)
                .where(
                    # Literal, so the partial index on unfinished operations applies.
                    text(f"environments.status IN {OPERATIONS}"),
                    or_(EnvironmentRow.lease_expires_at.is_(None), EnvironmentRow.lease_expires_at < func.now()),
                )
                .order_by(EnvironmentRow.updated_at)
                .limit(batch)
            )
        ).all()
    async with anyio.create_task_group() as group:
        for environment_id in due:
            group.start_soon(_advance, runtime, environment_id, owner)


async def _advance(runtime: Runtime, environment_id: str, owner: str) -> None:
    try:
        await advance(runtime, environment_id, owner=owner)
    except Exception as error:
        logger.warning(
            "Environment operation dispatch failed",
            extra={"environment_id": environment_id, "error_type": type(error).__name__},
        )


def maintenance_sweep(runtime: Runtime) -> Sweep:
    settings = runtime.settings.environments
    owner = new_object_id("ctl")
    return Sweep(
        name="maintain_environments",
        every=settings.scan_seconds,
        run=partial(maintain_environments, runtime, owner=owner),
        # Two claims' worth: the dispatched calls are bounded by their deadlines and publication.
        timeout=settings.operation_seconds * 2 + 30,
    )

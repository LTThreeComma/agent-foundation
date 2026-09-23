"""Desired thread mounts and the immutable mount set a run freezes at acceptance.

The frozen `runs.environment_mounts` of accepted and running runs is the durable active-use evidence that
stop and destroy check under the environment lock; acceptance takes the same locks before installing it.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import now
from a13n_service.infra.errors import ServiceError, conflict, not_found
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.environment_templates.service import usable_template
from a13n_service.runs.environments.tables import EnvironmentRow, ThreadEnvironmentRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import ThreadRow

PRIMARY = "workspace"


async def desired_mounts(session: AsyncSession, thread_id: str) -> list[ThreadEnvironmentRow]:
    return list(
        (
            await session.scalars(
                select(ThreadEnvironmentRow)
                .where(ThreadEnvironmentRow.thread_id == thread_id)
                .order_by(ThreadEnvironmentRow.name)
            )
        ).all()
    )


def _mount(thread: ThreadRow, environment_id: str, name: str, working_directory: str | None) -> ThreadEnvironmentRow:
    return ThreadEnvironmentRow(
        thread_id=thread.id,
        environment_id=environment_id,
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        name=name,
        working_directory=working_directory,
    )


async def copy_desired(session: AsyncSession, origin: ThreadRow, thread: ThreadRow) -> None:
    """Fork and child threads share their origin's environments; the caller holds the origin thread lock."""
    for mount in await desired_mounts(session, origin.id):
        session.add(_mount(thread, mount.environment_id, mount.name, mount.working_directory))
    await session.flush()


def require_usable(environment: EnvironmentRow, principal_id: str) -> None:
    """New use refuses retiring instances, unresolved permanent failures and other principals' devices."""
    if environment.owner_principal_id not in {None, principal_id}:
        raise ServiceError("forbidden", "Private environments are usable only by their owner", {"id": environment.id})
    if environment.status in {"deleting", "deleted"}:
        raise conflict("environment", environment.id, f"environment_{environment.status}")
    if environment.failure is not None and environment.failure.get("permanent"):
        raise conflict("environment", environment.id, "environment_failed")


async def _reserve_primary(session: AsyncSession, thread: ThreadRow, template_id: str, principal_id: str) -> None:
    """Reserve a creating instance and its mount; the create operation reads the template when it runs."""
    template = await usable_template(session, thread.workspace_id, template_id)
    environment = EnvironmentRow(
        id=new_object_id("env"),
        organization_id=thread.organization_id,
        workspace_id=thread.workspace_id,
        provider_id=template.provider_id,
        provider_identity={"type": template.provider_type},
        template_id=template.id,
        name=f"{thread.id}/{PRIMARY}",
        status="creating",
        generation=1,
        operation_id=new_object_id("envop"),
        operation_started_at=await now(session),
        created_by_id=principal_id,
    )
    session.add(environment)
    await session.flush()
    session.add(_mount(thread, environment.id, PRIMARY, None))
    await session.flush()


async def freeze_mounts(
    session: AsyncSession, thread: ThreadRow, *, principal_id: str, template_id: str | None
) -> list[dict]:
    """The mount set a new run uses. The caller holds the thread lock; environments lock in ID order."""
    mounts = await desired_mounts(session, thread.id)
    if template_id is not None and all(mount.name != PRIMARY for mount in mounts):
        await _reserve_primary(session, thread, template_id, principal_id)
        mounts = await desired_mounts(session, thread.id)
    await lock_environments(session, [mount.environment_id for mount in mounts], principal_id=principal_id)
    return [
        EnvironmentMount(
            name=mount.name, environment_id=mount.environment_id, working_directory=mount.working_directory
        ).model_dump(mode="json")
        for mount in mounts
    ]


async def lock_environments(
    session: AsyncSession, environment_ids: Sequence[str], *, principal_id: str
) -> list[EnvironmentRow]:
    ids = sorted(set(environment_ids))
    if not ids:
        return []
    rows = (
        await session.scalars(
            select(EnvironmentRow)
            .where(EnvironmentRow.id.in_(ids))
            .order_by(EnvironmentRow.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    found = {row.id for row in rows}
    for missing in ids:
        if missing not in found:
            raise not_found("environment", missing)
    for row in rows:
        require_usable(row, principal_id)
    return list(rows)

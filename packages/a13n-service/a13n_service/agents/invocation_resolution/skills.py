"""Skill preparation and freezing for Agent invocations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam import (
    AuthenticatedActor,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.skills.domain import SkillRevisionLock

from ..domain import (
    ResolvedSkillBinding,
    SkillSelection,
)
from ..errors import (
    agent_revision_not_executable,
)
from ..skill_resolution import (
    SkillSelectionInvalid,
    resolve_skill_locks_from_bindings,
    resolve_skill_locks_from_selections,
)


async def prepare_skills(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    retained: tuple[ResolvedSkillBinding, ...] | None,
    lock: bool = False,
) -> tuple[SkillRevisionLock, ...]:
    if not selections:
        return ()
    await authorize_workspace(
        session,
        actor=actor,
        workspace_id=workspace_id,
        action=WorkspaceAction.skill_read,
    )
    try:
        if retained is not None:
            if tuple((item.skill_key, item.version) for item in retained) != tuple(
                (item.skill_key, item.version) for item in selections
            ):
                raise SkillSelectionInvalid
            return await resolve_skill_locks_from_bindings(
                session,
                organization_id=organization_id,
                workspace_id=workspace_id,
                bindings=retained,
                lock=lock,
            )
        return await resolve_skill_locks_from_selections(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            selections=selections,
            lock=lock,
        )
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error


async def validate_retained_skills(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    locks: tuple[SkillRevisionLock, ...],
) -> None:
    """Check a successor's retained selections once, without locking configuration."""

    # A successor retains exact versions even when the source selected current.
    try:
        observed = await resolve_skill_locks_from_bindings(
            session,
            organization_id=organization_id,
            workspace_id=workspace_id,
            bindings=tuple(
                ResolvedSkillBinding(skill_id=item.skill_id, skill_key=item.skill_key, version=item.version)
                for item in locks
            ),
        )
        if observed != locks:
            raise SkillSelectionInvalid
    except SkillSelectionInvalid as error:
        raise agent_revision_not_executable("skill_selection_invalid") from error

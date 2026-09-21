"""Stable Skill authoring bindings and exact Run revision selection."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.skills.domain import SkillPackageManifest, SkillRevisionLock
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord

from .domain import ResolvedSkillBinding, SkillSelection


class SkillSelectionInvalid(RuntimeError):
    """A selected Skill cannot be resolved without changing its meaning."""


async def resolve_skill_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    lock: bool = False,
) -> tuple[ResolvedSkillBinding, ...]:
    """Resolve public keys to stable active identities for an AgentRevision."""

    bindings, _ = await _resolve_bindings(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        selections=selections,
        lock=lock,
    )
    await _load_pinned_revisions(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        for_update=lock,
    )
    return bindings


async def _resolve_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    lock: bool,
) -> tuple[tuple[ResolvedSkillBinding, ...], dict[str, SkillRecord]]:
    if not selections:
        return (), {}
    keys = tuple(item.skill_key for item in selections)
    query = (
        select(SkillRecord)
        .where(
            SkillRecord.organization_id == organization_id,
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.key.in_(keys),
            SkillRecord.deleted_at.is_(None),
        )
        .order_by(SkillRecord.id)
    )
    records = tuple(await session.scalars(query.with_for_update(read=True) if lock else query))
    by_key = {record.key: record for record in records}
    if set(by_key) != set(keys):
        raise SkillSelectionInvalid
    bindings = tuple(
        ResolvedSkillBinding(
            skill_id=by_key[selection.skill_key].id,
            skill_key=selection.skill_key,
            version=selection.version,
        )
        for selection in selections
    )
    if len({item.skill_id for item in bindings}) != len(bindings):
        raise SkillSelectionInvalid
    return bindings, {record.id: record for record in records}


async def resolve_skill_locks_from_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
    lock: bool = False,
) -> tuple[SkillRevisionLock, ...]:
    """Resolve frozen AgentRevision policies to exact active revisions."""

    if not bindings:
        return ()
    _require_unique_bindings(bindings)
    records = await _load_active_skills_by_id(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        skill_ids=tuple(item.skill_id for item in bindings),
        for_update=lock,
    )
    if set(records) != {item.skill_id for item in bindings}:
        raise SkillSelectionInvalid
    if any(records[item.skill_id].key != item.skill_key for item in bindings):
        raise SkillSelectionInvalid
    return await _resolve_locks(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        skills=records,
        lock=lock,
    )


async def resolve_skill_locks_from_selections(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
    lock: bool = False,
) -> tuple[SkillRevisionLock, ...]:
    """Resolve one Run override from active keys directly to exact revisions."""

    bindings, records = await _resolve_bindings(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        selections=selections,
        lock=lock,
    )
    return await _resolve_locks(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        skills=records,
        lock=lock,
    )


async def _resolve_locks(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
    skills: dict[str, SkillRecord],
    lock: bool = False,
) -> tuple[SkillRevisionLock, ...]:
    pinned = await _load_pinned_revisions(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        for_update=lock,
    )
    default_ids = tuple(skills[binding.skill_id].default_revision_id for binding in bindings if binding.version is None)
    default_query = select(SkillRevisionRecord).where(
        SkillRevisionRecord.organization_id == organization_id,
        SkillRevisionRecord.workspace_id == workspace_id,
        SkillRevisionRecord.id.in_(default_ids),
    )
    defaults = (
        tuple(await session.scalars(default_query.with_for_update(read=True) if lock else default_query))
        if default_ids
        else ()
    )
    by_default_id = {record.id: record for record in defaults}
    result: list[SkillRevisionLock] = []
    for binding in bindings:
        revision = (
            pinned[(binding.skill_id, binding.version)]
            if binding.version is not None
            else by_default_id.get(skills[binding.skill_id].default_revision_id)
        )
        if revision is None or revision.skill_id != binding.skill_id:
            raise SkillSelectionInvalid
        _validate_revision(revision, binding.skill_key)
        result.append(
            SkillRevisionLock(
                skill_id=binding.skill_id,
                skill_key=binding.skill_key,
                skill_revision_id=revision.id,
                version=revision.version,
                content_digest=revision.content_digest,
            )
        )
    return tuple(result)


async def _load_active_skills_by_id(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_ids: tuple[str, ...],
    for_update: bool = False,
) -> dict[str, SkillRecord]:
    query = select(SkillRecord).where(
        SkillRecord.organization_id == organization_id,
        SkillRecord.workspace_id == workspace_id,
        SkillRecord.id.in_(skill_ids),
        SkillRecord.deleted_at.is_(None),
    )
    if for_update:
        query = query.with_for_update(read=True)
    records = tuple((await session.scalars(query)).all())
    return {record.id: record for record in records}


async def _load_pinned_revisions(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
    for_update: bool = False,
) -> dict[tuple[str, int], SkillRevisionRecord]:
    requested = tuple((item.skill_id, item.version) for item in bindings if item.version is not None)
    if not requested:
        return {}
    query = select(SkillRevisionRecord).where(
        SkillRevisionRecord.organization_id == organization_id,
        SkillRevisionRecord.workspace_id == workspace_id,
        tuple_(SkillRevisionRecord.skill_id, SkillRevisionRecord.version).in_(requested),
    )
    if for_update:
        query = query.with_for_update(read=True)
    records = tuple((await session.scalars(query)).all())
    by_identity = {(record.skill_id, record.version): record for record in records}
    if set(by_identity) != set(requested):
        raise SkillSelectionInvalid
    for binding in bindings:
        if binding.version is not None:
            _validate_revision(by_identity[(binding.skill_id, binding.version)], binding.skill_key)
    return by_identity


def _validate_revision(revision: SkillRevisionRecord, skill_key: str) -> None:
    try:
        manifest = SkillPackageManifest.model_validate(revision.manifest)
    except ValidationError as error:
        raise SkillSelectionInvalid from error
    if manifest.skill_name != skill_key or manifest.content_digest != revision.content_digest:
        raise SkillSelectionInvalid


def _require_unique_bindings(bindings: tuple[ResolvedSkillBinding, ...]) -> None:
    skill_ids = tuple(item.skill_id for item in bindings)
    skill_keys = tuple(item.skill_key for item in bindings)
    if len(skill_ids) != len(set(skill_ids)) or len(skill_keys) != len(set(skill_keys)):
        raise SkillSelectionInvalid

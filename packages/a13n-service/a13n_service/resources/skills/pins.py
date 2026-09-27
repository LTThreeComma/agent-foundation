"""Agent revisions pin exact skill revisions; a pin is validated when it is written."""

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import at_field, conflict, not_found
from a13n_service.resources.skills.schemas import SkillPin
from a13n_service.resources.skills.tables import SkillRevisionRow, SkillRow


async def require_pins(session: AsyncSession, workspace_id: str, pins: Mapping[str, SkillPin]) -> None:
    """New pins, by the field path a failure names, may name only existing revisions of this workspace's
    unarchived skills."""
    if not pins:
        return
    rows = (
        await session.execute(
            select(SkillRevisionRow.id, SkillRow.key, SkillRow.archived_at)
            .join(SkillRow, SkillRow.id == SkillRevisionRow.skill_id)
            .where(
                SkillRevisionRow.workspace_id == workspace_id,
                SkillRevisionRow.id.in_({pin.revision_id for pin in pins.values()}),
            )
        )
    ).all()
    found = {row.id: row for row in rows}
    for path, pin in pins.items():
        with at_field(path):
            row = found.get(pin.revision_id)
            if row is None or row.key != pin.skill:
                raise not_found(SkillRevisionRow.KIND, pin.revision_id)
            if row.archived_at is not None:
                raise conflict(SkillRow.KIND, pin.skill, "archived")

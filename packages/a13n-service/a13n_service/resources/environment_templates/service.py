"""Environment template lookups other owners use; management operations live alongside."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import disabled, not_found
from a13n_service.resources.environment_providers.tables import EnvironmentProviderRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow


@dataclass(frozen=True, slots=True)
class UsableTemplate:
    id: str
    provider_id: str
    provider_type: str


async def usable_template(session: AsyncSession, workspace_id: str, template_id: str) -> UsableTemplate:
    """A template new environments may be created from: enabled, in this workspace, with an enabled provider."""
    row = await session.scalar(
        select(EnvironmentTemplateRow).where(
            EnvironmentTemplateRow.workspace_id == workspace_id, EnvironmentTemplateRow.id == template_id
        )
    )
    if row is None:
        raise not_found("environment_template", template_id)
    provider = await session.get(EnvironmentProviderRow, row.provider_id)
    if not row.enabled or provider is None or not provider.enabled:
        raise disabled("environment_template", template_id)
    return UsableTemplate(id=row.id, provider_id=provider.id, provider_type=provider.type)

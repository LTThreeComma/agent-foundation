"""One Model capture across every role in an accepted Agent graph."""

from __future__ import annotations

from contextlib import nullcontext

from a13n_harness.toolsets.file_media import NativeInputMediaKind
from a13n_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.models.domain import MEDIA_KINDS, MediaUnderstandingSelection
from a13n_service.models.domain import Model as ModelResource
from a13n_service.models.media_defaults import read_media_defaults, require_media_capability
from a13n_service.models.runtime import AcceptedModelSelector, PreparedModelExecution
from a13n_service.models.service import ModelError
from a13n_service.models.settings import JsonObject, effective_settings
from a13n_service.storage import short_session

logger = get_logger(__name__)


class InvocationModelSelection:
    """Share resource observations, not role settings, for one bounded selection operation."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        model_selector: AcceptedModelSelector,
        *,
        organization_id: str,
        workspace_id: str,
        session: AsyncSession | None = None,
    ) -> None:
        self._sessions = sessions
        self._session = session
        self._model_selector = model_selector
        self._organization_id = organization_id
        self._workspace_id = workspace_id
        self._defaults: MediaUnderstandingSelection | None = None
        self._models: dict[tuple[str, str], ModelResource | ModelError] = {}

    async def resolve(
        self, selected: MediaUnderstandingSelection, *, include_defaults: bool = True
    ) -> dict[NativeInputMediaKind, PreparedModelExecution]:
        """Reject an unusable explicit selection; skip an unusable Workspace default."""

        defaults = await self._workspace_defaults() if include_defaults else MediaUnderstandingSelection()
        resolved: dict[NativeInputMediaKind, PreparedModelExecution] = {}
        for kind in MEDIA_KINDS:
            explicit = getattr(selected, kind)
            key = explicit if explicit is not None else getattr(defaults, kind)
            if key is None:
                continue
            try:
                prepared = await self.prepare(model_key=key, settings={})
                require_media_capability(prepared.resource, kind)
            except ModelError as error:
                failure = error
            else:
                resolved[kind] = prepared
                continue
            if explicit is not None:
                raise failure
            # A Workspace default that became unusable must not block every Run in the
            # Workspace; the view tool reports media_understanding_unavailable instead.
            logger.warning(
                "media_understanding_default_skipped",
                extra={
                    "workspace_id": self._workspace_id,
                    "media_kind": kind,
                    "model_key": key,
                    "reason": failure.code,
                },
            )
        return resolved

    async def _workspace_defaults(self) -> MediaUnderstandingSelection:
        if self._defaults is None:
            async with (
                nullcontext(self._session) if self._session is not None else short_session(self._sessions)
            ) as session:
                self._defaults = await read_media_defaults(session, self._workspace_id)
        return self._defaults

    async def prepare(
        self,
        *,
        model_key: str | None = None,
        model_id: str | None = None,
        settings: JsonObject,
        settings_override: JsonObject | None = None,
    ) -> PreparedModelExecution:
        if (model_key is None) == (model_id is None):
            raise ValueError("exactly one Model selector is required")
        key = ("key", model_key.casefold()) if model_key is not None else ("id", str(model_id))
        if key not in self._models:
            try:
                prepared = await self._model_selector.prepare(
                    organization_id=self._organization_id,
                    workspace_id=self._workspace_id,
                    model_key=model_key,
                    model_id=model_id,
                    settings={},
                    session=self._session,
                )
            except ModelError as error:
                self._models[key] = error
            else:
                resource = prepared.resource
                # An ID and a normalized key are aliases of the same first observation,
                # including when a key changed between graph visits.
                selected = self._models.setdefault(("id", resource.id), resource)
                self._models[("key", resource.key.casefold())] = selected
                self._models[key] = selected
        model = self._models[key]
        if isinstance(model, ModelError):
            raise model
        layers = (settings,) if settings_override is None else (settings, settings_override)
        effective_settings(model.model_api, model.settings, *layers)
        return PreparedModelExecution(model, layers)

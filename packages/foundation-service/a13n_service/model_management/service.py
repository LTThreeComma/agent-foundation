"""Transactional ModelConfig application service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from time import monotonic
from typing import Any, Protocol, cast

from anyio import fail_after
from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.domain import PrincipalType
from a13n_service.iam.models import SecurityAuditRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import transaction

from .cursors import CursorError, decode_model_cursor, encode_model_cursor
from .domain import (
    InvokingUserSecretCredential,
    ModelConfigCollection,
    ModelConfigCopy,
    ModelConfigCreate,
    ModelConfigPatch,
    ModelConfigResource,
    ModelConnectionTestResult,
    ModelCredential,
    ModelReferenceCollection,
    WorkspaceSecretCredential,
    new_model_config_id,
)
from .endpoint_policy import EndpointPolicy, EndpointPolicyError
from .models import ModelConfigRecord, ModelIdempotencyRecord
from .providers import ProviderDefinitionCollection, ProviderRegistry, ValidatedProviderSelection

_CREDENTIAL_ADAPTER = TypeAdapter(ModelCredential)


class ModelManagementError(Exception):
    """Safe stable error raised by the Model Management application layer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ModelReferenceReader(Protocol):
    async def count(self, session: AsyncSession, *, organization_id: str, workspace_id: str, model_id: str) -> int: ...

    async def list(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        workspace_id: str,
        model_id: str,
        limit: int,
        cursor: str | None,
    ) -> ModelReferenceCollection: ...


class CandidateConnectionTester(Protocol):
    def __call__(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        provider_type: str,
        model_name: str,
        credential: ModelCredential,
        selection: ValidatedProviderSelection,
    ) -> Awaitable[None]: ...


def _audit_failed_attempt(
    action: str, *, model_id_argument: str | None = None
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Persist one failure event without placing protected request fields in audit data."""

    def decorate(method: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(method)
        async def audited(service: ModelConfigService, *args: object, **kwargs: object) -> Any:
            try:
                return await method(service, *args, **kwargs)
            except Exception:
                actor = cast(AuthenticatedActor, kwargs["actor"])
                workspace_id = cast(str, kwargs["workspace_id"])
                model_id = cast(str | None, kwargs.get(model_id_argument)) if model_id_argument is not None else None
                audit_action = action
                request = kwargs.get("request")
                if (
                    action == "model_config.update"
                    and isinstance(request, ModelConfigPatch)
                    and request.model_fields_set == {"enabled"}
                ):
                    audit_action = f"model_config.{'enable' if request.enabled else 'disable'}"
                await service._record_failed_attempt(
                    actor=actor,
                    workspace_id=workspace_id,
                    model_id=model_id,
                    action=audit_action,
                )
                raise

        return audited

    return decorate


class ModelConfigService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
        endpoint_policy: EndpointPolicy,
        *,
        clock: Callable[[], datetime] | None = None,
        resolve_dns_on_save: bool = True,
        reference_reader: ModelReferenceReader | None = None,
        connection_tester: CandidateConnectionTester | None = None,
        connection_test_timeout_seconds: float = 15,
    ) -> None:
        self._sessions = sessions
        self._registry = registry
        self._endpoint_policy = endpoint_policy
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resolve_dns_on_save = resolve_dns_on_save
        self._reference_reader = reference_reader
        self._connection_tester = connection_tester
        self._connection_test_timeout_seconds = connection_test_timeout_seconds

    async def provider_definitions(self, *, actor: AuthenticatedActor) -> ProviderDefinitionCollection:
        async with transaction(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=actor.boundary_workspace_id,
                action=WorkspaceAction.models_read,
            )
        return ProviderDefinitionCollection(items=self._registry.definitions())

    @_audit_failed_attempt("model_config.create")
    async def create(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: ModelConfigCreate,
        idempotency_key: str,
    ) -> tuple[ModelConfigResource, bool]:
        """Create or replay one ModelConfig. The boolean is true for a replay."""

        key = _validate_idempotency_key(idempotency_key)
        await self._preauthorize_manage(actor=actor, workspace_id=workspace_id)
        selection = await self._validate_provider(request)
        request_sha256 = _canonical_sha256(request.model_dump(mode="json"))
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                replay = await session.scalar(
                    select(ModelIdempotencyRecord).where(
                        ModelIdempotencyRecord.workspace_id == workspace.workspace_id,
                        ModelIdempotencyRecord.principal_type == actor.principal.principal_type.value,
                        ModelIdempotencyRecord.principal_id == actor.principal.principal_id,
                        ModelIdempotencyRecord.operation == "create",
                        ModelIdempotencyRecord.idempotency_key == key,
                    )
                )
                if replay is not None:
                    if _as_utc(replay.expires_at) <= now:
                        await session.delete(replay)
                        await session.flush()
                        replay = None
                if replay is not None:
                    if replay.request_sha256 != request_sha256:
                        raise ModelManagementError(
                            "idempotency_conflict",
                            "The idempotency key was already used with different input.",
                            status_code=409,
                        )
                    record = await session.scalar(
                        select(ModelConfigRecord).where(
                            ModelConfigRecord.organization_id == workspace.organization_id,
                            ModelConfigRecord.workspace_id == workspace.workspace_id,
                            ModelConfigRecord.id == replay.model_id,
                        )
                    )
                    if record is None:
                        raise ModelManagementError(
                            "idempotency_result_unavailable",
                            "The prior idempotent result is no longer available.",
                            status_code=409,
                        )
                    session.add(
                        _audit_record(
                            actor=actor,
                            organization_id=workspace.organization_id,
                            workspace_id=workspace.workspace_id,
                            model_id=record.id,
                            action="model_config.create",
                            now=now,
                            details={"idempotent_replay": True},
                        )
                    )
                    return record.to_resource(), True

                await _require_eligible_credential(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    actor=actor,
                    credential=request.credential,
                )
                model_id = new_model_config_id()
                record = ModelConfigRecord(
                    id=model_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    name=request.name,
                    normalized_name=_normalize_name(request.name),
                    description=request.description,
                    provider_type=request.provider_type,
                    model_name=request.model_name,
                    base_url=selection.base_url,
                    credential=_CREDENTIAL_ADAPTER.dump_python(request.credential, mode="json"),
                    provider_config=selection.provider_config,
                    capabilities=selection.capabilities.model_dump(mode="json"),
                    capability_source=selection.capability_source.value,
                    enabled=request.enabled,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
                session.add(
                    ModelIdempotencyRecord(
                        id=new_object_id("idem"),
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        principal_type=actor.principal.principal_type.value,
                        principal_id=actor.principal.principal_id,
                        operation="create",
                        idempotency_key=key,
                        request_sha256=request_sha256,
                        model_id=model_id,
                        created_at=now,
                        expires_at=now + timedelta(hours=24),
                    )
                )
                session.add(
                    _audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        model_id=model_id,
                        action="model_config.create",
                        now=now,
                    )
                )
                await session.flush()
                return record.to_resource(), False
        except IntegrityError as error:
            replay = await self._replay_after_integrity(
                actor=actor,
                workspace_id=workspace_id,
                operation="create",
                idempotency_key=key,
                request_sha256=request_sha256,
                now=now,
            )
            if replay is not None:
                return replay, True
            raise ModelManagementError(
                "model_name_conflict",
                "A model configuration with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    async def get(self, *, actor: AuthenticatedActor, workspace_id: str, model_id: str) -> ModelConfigResource:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            record = await session.scalar(
                select(ModelConfigRecord).where(
                    ModelConfigRecord.organization_id == workspace.organization_id,
                    ModelConfigRecord.workspace_id == workspace.workspace_id,
                    ModelConfigRecord.id == model_id,
                )
            )
            if record is None:
                raise ModelManagementError("model_not_found", "The model configuration was not found.", status_code=404)
            return record.to_resource()

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int = 50,
        cursor: str | None = None,
        name: str | None = None,
        provider_type: str | None = None,
        enabled: bool | None = None,
    ) -> ModelConfigCollection:
        if limit < 1 or limit > 100:
            raise ModelManagementError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        if name is not None and (not name.strip() or len(name) > 128):
            raise ModelManagementError("invalid_request", "name search is invalid.", status_code=400)
        if provider_type is not None:
            try:
                self._registry.definition(provider_type)
            except ValueError as error:
                raise ModelManagementError(
                    "invalid_request", "provider_type is not a trusted provider.", status_code=400
                ) from error
        scope: dict[str, object] = {
            "workspace_id": workspace_id,
            "principal_type": actor.principal.principal_type.value,
            "principal_id": actor.principal.principal_id,
            "name": name,
            "provider_type": provider_type,
            "enabled": enabled,
        }
        try:
            position = decode_model_cursor(cursor, scope=scope) if cursor is not None else None
        except CursorError as error:
            raise ModelManagementError(
                "invalid_cursor", "The collection cursor is invalid.", status_code=400
            ) from error
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            query = select(ModelConfigRecord).where(
                ModelConfigRecord.organization_id == workspace.organization_id,
                ModelConfigRecord.workspace_id == workspace.workspace_id,
            )
            if name is not None:
                escaped = name.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                query = query.where(ModelConfigRecord.name.ilike(f"%{escaped}%", escape="\\"))
            if provider_type is not None:
                query = query.where(ModelConfigRecord.provider_type == provider_type)
            if enabled is not None:
                query = query.where(ModelConfigRecord.enabled == enabled)
            if position is not None:
                updated_at, model_id = position
                query = query.where(
                    or_(
                        ModelConfigRecord.updated_at < updated_at,
                        and_(ModelConfigRecord.updated_at == updated_at, ModelConfigRecord.id < model_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(ModelConfigRecord.updated_at.desc(), ModelConfigRecord.id.desc()).limit(
                            limit + 1
                        )
                    )
                ).all()
            )
            page = records[:limit]
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_model_cursor(updated_at=page[-1].updated_at, model_id=page[-1].id, scope=scope)
            return ModelConfigCollection(items=tuple(record.to_resource() for record in page), next_cursor=next_cursor)

    @_audit_failed_attempt("model_config.update", model_id_argument="model_id")
    async def patch(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        request: ModelConfigPatch,
        if_match: str,
    ) -> ModelConfigResource:
        await self._preauthorize_manage(actor=actor, workspace_id=workspace_id)
        current = await self.get(actor=actor, workspace_id=workspace_id, model_id=model_id)
        if current.strong_etag() != if_match:
            raise ModelManagementError(
                "precondition_failed", "The ModelConfig representation has changed.", status_code=412
            )
        capabilities = (
            request.capabilities
            if "capabilities" in request.model_fields_set
            else (current.capabilities if current.capability_source.value == "manual_override" else None)
        )
        merged = ModelConfigCreate(
            name=cast(str, request.name) if "name" in request.model_fields_set else current.name,
            description=request.description if "description" in request.model_fields_set else current.description,
            provider_type=(
                cast(str, request.provider_type)
                if "provider_type" in request.model_fields_set
                else current.provider_type
            ),
            model_name=(
                cast(str, request.model_name) if "model_name" in request.model_fields_set else current.model_name
            ),
            credential=(
                cast(ModelCredential, request.credential)
                if "credential" in request.model_fields_set
                else current.credential
            ),
            provider_config=(
                cast(dict[str, object], request.provider_config)
                if "provider_config" in request.model_fields_set
                else current.provider_config
            ),
            capabilities=capabilities,
            enabled=(cast(bool, request.enabled) if "enabled" in request.model_fields_set else current.enabled),
        )
        selection = await self._validate_provider(merged)
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                record = await session.scalar(
                    select(ModelConfigRecord)
                    .where(
                        ModelConfigRecord.organization_id == workspace.organization_id,
                        ModelConfigRecord.workspace_id == workspace.workspace_id,
                        ModelConfigRecord.id == model_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise ModelManagementError(
                        "model_not_found", "The model configuration was not found.", status_code=404
                    )
                before = record.to_resource()
                if before.strong_etag() != if_match:
                    raise ModelManagementError(
                        "precondition_failed", "The ModelConfig representation has changed.", status_code=412
                    )
                await _require_eligible_credential(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    actor=actor,
                    credential=merged.credential,
                )
                updates: dict[str, object] = {
                    "name": merged.name,
                    "normalized_name": _normalize_name(merged.name),
                    "description": merged.description,
                    "provider_type": merged.provider_type,
                    "model_name": merged.model_name,
                    "base_url": selection.base_url,
                    "credential": _CREDENTIAL_ADAPTER.dump_python(merged.credential, mode="json"),
                    "provider_config": selection.provider_config,
                    "capabilities": selection.capabilities.model_dump(mode="json"),
                    "capability_source": selection.capability_source.value,
                    "enabled": merged.enabled,
                }
                changed_fields = sorted(
                    key for key, value in updates.items() if key != "normalized_name" and getattr(record, key) != value
                )
                if not changed_fields:
                    session.add(
                        _audit_record(
                            actor=actor,
                            organization_id=workspace.organization_id,
                            workspace_id=workspace.workspace_id,
                            model_id=model_id,
                            action="model_config.update",
                            now=now,
                            details={"changed_fields": []},
                        )
                    )
                    return before
                for key, value in updates.items():
                    setattr(record, key, value)
                record.updated_by_type = actor.principal.principal_type.value
                record.updated_by_id = actor.principal.principal_id
                record.updated_at = now
                audit_action = (
                    f"model_config.{'enable' if merged.enabled else 'disable'}"
                    if changed_fields == ["enabled"]
                    else "model_config.update"
                )
                session.add(
                    _audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        model_id=model_id,
                        action=audit_action,
                        now=now,
                        details={"changed_fields": changed_fields[:32]},
                    )
                )
                await session.flush()
                return record.to_resource()
        except IntegrityError as error:
            raise ModelManagementError(
                "model_name_conflict",
                "A model configuration with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    @_audit_failed_attempt("model_config.copy", model_id_argument="model_id")
    async def copy(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        request: ModelConfigCopy,
        idempotency_key: str,
    ) -> tuple[ModelConfigResource, bool]:
        key = _validate_idempotency_key(idempotency_key)
        await self._preauthorize_manage(actor=actor, workspace_id=workspace_id)
        source = await self.get(actor=actor, workspace_id=workspace_id, model_id=model_id)
        if source.base_url is not None:
            try:
                await self._endpoint_policy.validate(source.base_url, resolve_dns=self._resolve_dns_on_save)
            except EndpointPolicyError as error:
                raise ModelManagementError(
                    "invalid_model_configuration", "The source model configuration is no longer valid.", status_code=400
                ) from error
        request_sha256 = _canonical_sha256({"source_model_id": model_id, "request": request.model_dump(mode="json")})
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await _authorize(
                    session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
                )
                replay = await session.scalar(
                    select(ModelIdempotencyRecord).where(
                        ModelIdempotencyRecord.workspace_id == workspace.workspace_id,
                        ModelIdempotencyRecord.principal_type == actor.principal.principal_type.value,
                        ModelIdempotencyRecord.principal_id == actor.principal.principal_id,
                        ModelIdempotencyRecord.operation == "copy",
                        ModelIdempotencyRecord.idempotency_key == key,
                    )
                )
                if replay is not None:
                    if _as_utc(replay.expires_at) <= now:
                        await session.delete(replay)
                        await session.flush()
                        replay = None
                if replay is not None:
                    if replay.request_sha256 != request_sha256:
                        raise ModelManagementError(
                            "idempotency_conflict",
                            "The idempotency key was already used with different input.",
                            status_code=409,
                        )
                    replay_record = await session.scalar(
                        select(ModelConfigRecord).where(
                            ModelConfigRecord.organization_id == workspace.organization_id,
                            ModelConfigRecord.workspace_id == workspace.workspace_id,
                            ModelConfigRecord.id == replay.model_id,
                        )
                    )
                    if replay_record is None:
                        raise ModelManagementError(
                            "idempotency_result_unavailable",
                            "The prior idempotent result is no longer available.",
                            status_code=409,
                        )
                    session.add(
                        _audit_record(
                            actor=actor,
                            organization_id=workspace.organization_id,
                            workspace_id=workspace.workspace_id,
                            model_id=replay_record.id,
                            action="model_config.copy",
                            now=now,
                            details={"idempotent_replay": True},
                        )
                    )
                    return replay_record.to_resource(), True
                source_record = await session.scalar(
                    select(ModelConfigRecord).where(
                        ModelConfigRecord.organization_id == workspace.organization_id,
                        ModelConfigRecord.workspace_id == workspace.workspace_id,
                        ModelConfigRecord.id == model_id,
                    )
                )
                if source_record is None:
                    raise ModelManagementError(
                        "model_not_found", "The model configuration was not found.", status_code=404
                    )
                await _require_eligible_credential(
                    session,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    actor=actor,
                    credential=source_record.to_resource().credential,
                )
                copied_id = new_model_config_id()
                copied = ModelConfigRecord(
                    id=copied_id,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    name=request.name,
                    normalized_name=_normalize_name(request.name),
                    description=request.description,
                    provider_type=source_record.provider_type,
                    model_name=source_record.model_name,
                    base_url=source_record.base_url,
                    credential=dict(source_record.credential),
                    provider_config=dict(source_record.provider_config),
                    capabilities=dict(source_record.capabilities),
                    capability_source=source_record.capability_source,
                    enabled=request.enabled,
                    created_by_type=actor.principal.principal_type.value,
                    created_by_id=actor.principal.principal_id,
                    updated_by_type=actor.principal.principal_type.value,
                    updated_by_id=actor.principal.principal_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(copied)
                session.add(
                    ModelIdempotencyRecord(
                        id=new_object_id("idem"),
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        principal_type=actor.principal.principal_type.value,
                        principal_id=actor.principal.principal_id,
                        operation="copy",
                        idempotency_key=key,
                        request_sha256=request_sha256,
                        model_id=copied_id,
                        created_at=now,
                        expires_at=now + timedelta(hours=24),
                    )
                )
                session.add(
                    _audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace.workspace_id,
                        model_id=copied_id,
                        action="model_config.copy",
                        now=now,
                    )
                )
                await session.flush()
                return copied.to_resource(), False
        except IntegrityError as error:
            replay = await self._replay_after_integrity(
                actor=actor,
                workspace_id=workspace_id,
                operation="copy",
                idempotency_key=key,
                request_sha256=request_sha256,
                now=now,
            )
            if replay is not None:
                return replay, True
            raise ModelManagementError(
                "model_name_conflict",
                "A model configuration with this name already exists in the Workspace.",
                status_code=409,
            ) from error

    @_audit_failed_attempt("model_config.delete", model_id_argument="model_id")
    async def delete(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        if_match: str,
    ) -> None:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            if self._reference_reader is None:
                raise ModelManagementError(
                    "model_reference_reader_unavailable",
                    "Model reference validation is unavailable.",
                    status_code=503,
                )
            record = await session.scalar(
                select(ModelConfigRecord)
                .where(
                    ModelConfigRecord.organization_id == workspace.organization_id,
                    ModelConfigRecord.workspace_id == workspace.workspace_id,
                    ModelConfigRecord.id == model_id,
                )
                .with_for_update()
            )
            if record is None:
                raise ModelManagementError("model_not_found", "The model configuration was not found.", status_code=404)
            if record.to_resource().strong_etag() != if_match:
                raise ModelManagementError(
                    "precondition_failed", "The ModelConfig representation has changed.", status_code=412
                )
            references = await self._reference_reader.count(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                model_id=model_id,
            )
            if references:
                raise ModelManagementError(
                    "model_in_use",
                    "The model configuration is referenced by executable Agent revisions.",
                    status_code=409,
                    details={"reference_count": references},
                )
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=model_id,
                    action="model_config.delete",
                    now=self._clock(),
                )
            )
            await session.delete(record)

    async def references(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ModelReferenceCollection:
        if limit < 1 or limit > 100:
            raise ModelManagementError("invalid_request", "limit must be between 1 and 100.", status_code=400)
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_read
            )
            if self._reference_reader is None:
                raise ModelManagementError(
                    "model_reference_reader_unavailable",
                    "Model reference validation is unavailable.",
                    status_code=503,
                )
            exists = await session.scalar(
                select(ModelConfigRecord.id).where(
                    ModelConfigRecord.organization_id == workspace.organization_id,
                    ModelConfigRecord.workspace_id == workspace.workspace_id,
                    ModelConfigRecord.id == model_id,
                )
            )
            if exists is None:
                raise ModelManagementError("model_not_found", "The model configuration was not found.", status_code=404)
            return await self._reference_reader.list(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                model_id=model_id,
                limit=limit,
                cursor=cursor,
            )

    @_audit_failed_attempt("model_config.test")
    async def test_candidate(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        request: ModelConfigCreate,
    ) -> ModelConnectionTestResult:
        await self._preauthorize_manage(actor=actor, workspace_id=workspace_id)
        if self._connection_tester is None:
            raise ModelManagementError(
                "model_connection_tester_unavailable", "Model connection testing is unavailable.", status_code=503
            )
        selection = await self._validate_provider(request)
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            await _require_eligible_credential(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace.workspace_id,
                actor=actor,
                credential=request.credential,
            )
            organization_id = workspace.organization_id
        started = monotonic()
        success = True
        code = "connection_succeeded"
        message = "The provider connection succeeded."
        try:
            with fail_after(self._connection_test_timeout_seconds):
                await self._connection_tester(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    provider_type=request.provider_type,
                    model_name=request.model_name,
                    credential=request.credential,
                    selection=selection,
                )
        except TimeoutError:
            success = False
            code = "connection_timeout"
            message = "The provider connection timed out."
        except Exception:
            success = False
            code = "connection_failed"
            message = "The provider connection failed."
        elapsed_ms = max(0, round((monotonic() - started) * 1000))
        async with transaction(self._sessions) as session:
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    model_id=None,
                    action="model_config.test",
                    now=self._clock(),
                    outcome="success" if success else "failure",
                )
            )
        return ModelConnectionTestResult(success=success, elapsed_ms=elapsed_ms, code=code, message=message)

    async def _replay_after_integrity(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        operation: str,
        idempotency_key: str,
        request_sha256: str,
        now: datetime,
    ) -> ModelConfigResource | None:
        async with transaction(self._sessions) as session:
            workspace = await _authorize(
                session, actor=actor, workspace_id=workspace_id, action=WorkspaceAction.models_manage
            )
            replay = await session.scalar(
                select(ModelIdempotencyRecord).where(
                    ModelIdempotencyRecord.workspace_id == workspace.workspace_id,
                    ModelIdempotencyRecord.principal_type == actor.principal.principal_type.value,
                    ModelIdempotencyRecord.principal_id == actor.principal.principal_id,
                    ModelIdempotencyRecord.operation == operation,
                    ModelIdempotencyRecord.idempotency_key == idempotency_key,
                    ModelIdempotencyRecord.expires_at > now,
                )
            )
            if replay is None:
                return None
            if replay.request_sha256 != request_sha256:
                raise ModelManagementError(
                    "idempotency_conflict",
                    "The idempotency key was already used with different input.",
                    status_code=409,
                )
            record = await session.scalar(
                select(ModelConfigRecord).where(
                    ModelConfigRecord.organization_id == workspace.organization_id,
                    ModelConfigRecord.workspace_id == workspace.workspace_id,
                    ModelConfigRecord.id == replay.model_id,
                )
            )
            if record is None:
                raise ModelManagementError(
                    "idempotency_result_unavailable",
                    "The prior idempotent result is no longer available.",
                    status_code=409,
                )
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=workspace.organization_id,
                    workspace_id=workspace.workspace_id,
                    model_id=record.id,
                    action=f"model_config.{operation}",
                    now=now,
                    details={"idempotent_replay": True},
                )
            )
            return record.to_resource()

    async def _preauthorize_manage(self, *, actor: AuthenticatedActor, workspace_id: str) -> None:
        async with transaction(self._sessions) as session:
            await _authorize(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.models_manage,
            )

    async def _record_failed_attempt(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        model_id: str | None,
        action: str,
    ) -> None:
        async with transaction(self._sessions) as session:
            organization_id = await session.scalar(
                select(WorkspaceRecord.organization_id).where(WorkspaceRecord.id == workspace_id)
            )
            session.add(
                _audit_record(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    model_id=model_id,
                    action=action,
                    now=self._clock(),
                    outcome="failure",
                )
            )

    async def _validate_provider(self, request: ModelConfigCreate) -> ValidatedProviderSelection:
        try:
            selection = self._registry.validate(
                provider_type=request.provider_type,
                model_name=request.model_name,
                provider_config=request.provider_config,
                credential=request.credential,
                capabilities=request.capabilities,
            )
            if selection.base_url is None:
                return selection
            normalized_url = await self._endpoint_policy.validate(
                selection.base_url, resolve_dns=self._resolve_dns_on_save
            )
        except (ValueError, EndpointPolicyError) as error:
            raise ModelManagementError(
                "invalid_model_configuration",
                "The model configuration is invalid.",
                status_code=400,
            ) from error
        provider_config = dict(selection.provider_config)
        if request.provider_type == "openai_compatible":
            provider_config["base_url"] = normalized_url
        elif request.provider_type == "azure_openai":
            provider_config["resource_endpoint"] = normalized_url
        return selection.model_copy(update={"base_url": normalized_url, "provider_config": provider_config})


async def _authorize(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ModelManagementError(
            "resource_not_found" if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            status_code=404 if error.concealed else 403,
        ) from error


async def _require_eligible_credential(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    actor: AuthenticatedActor,
    credential: ModelCredential,
) -> None:
    query = select(ManagedSecretRecord.id).where(
        ManagedSecretRecord.organization_id == organization_id,
        ManagedSecretRecord.workspace_id == workspace_id,
        ManagedSecretRecord.deleted_at.is_(None),
    )
    if isinstance(credential, WorkspaceSecretCredential):
        query = query.where(
            ManagedSecretRecord.id == credential.secret_id,
            ManagedSecretRecord.owner_type == "workspace",
            ManagedSecretRecord.owner_id == workspace_id,
        )
    elif isinstance(credential, InvokingUserSecretCredential):
        if actor.principal.principal_type is not PrincipalType.user:
            raise ModelManagementError(
                "credential_not_eligible",
                "The selected credential is not eligible for this operation.",
                status_code=400,
            )
        query = query.where(
            ManagedSecretRecord.owner_type == "user",
            ManagedSecretRecord.owner_id == actor.principal.principal_id,
            ManagedSecretRecord.key == credential.secret_key,
        )
    else:
        return
    if await session.scalar(query) is None:
        raise ModelManagementError(
            "credential_not_eligible",
            "The selected credential is not eligible for this operation.",
            status_code=400,
        )


def _audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str,
    model_id: str | None,
    action: str,
    now: datetime,
    outcome: str = "success",
    details: dict[str, object] | None = None,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="model_config",
        resource_id=model_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome=outcome,
        occurred_at=now,
        request_id=actor.request_id,
        details=details,
    )


def _normalize_name(name: str) -> str:
    return name.casefold()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_idempotency_key(value: str) -> str:
    if (
        not value
        or len(value.encode()) > 256
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise ModelManagementError(
            "invalid_idempotency_key",
            "Idempotency-Key must contain 1 through 256 printable ASCII characters.",
            status_code=400,
        )
    return value

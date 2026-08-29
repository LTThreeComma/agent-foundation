from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    SecurityAuditRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.model_management.domain import (
    InvokingUserSecretCredential,
    ModelConfigCopy,
    ModelConfigCreate,
    ModelConfigPatch,
    ModelReferenceCollection,
    PrincipalRef,
    WorkspaceSecretCredential,
)
from a13n_service.model_management.endpoint_policy import EndpointPolicy
from a13n_service.model_management.models import ModelConfigRecord
from a13n_service.model_management.providers import built_in_provider_registry
from a13n_service.model_management.service import ModelConfigService, ModelManagementError
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
VIEWER_ID = "usr_abcdef1234567890"
SECRET_ID = "sec_1234567890abcdef"


class NoReferences:
    async def count(self, session: AsyncSession, **_: object) -> int:
        del session
        return 0

    async def list(self, session: AsyncSession, **_: object) -> ModelReferenceCollection:
        del session
        return ModelReferenceCollection(items=(), next_cursor=None)


async def successful_connection_test(**_: object) -> None:
    return None


@pytest.fixture
async def model_service(tmp_path: Path) -> AsyncIterator[tuple[ModelConfigService, AsyncEngine]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "models.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", version=1, created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                version=1,
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        for user_id in (USER_ID, VIEWER_ID):
            session.add(
                UserRecord(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    normalized_email=f"{user_id}@example.com",
                    name=user_id,
                    status="active",
                    email_verified_at=NOW,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()
        for user_id in (USER_ID, VIEWER_ID):
            session.add(
                RoleBindingRecord(
                    id=f"rb_{user_id[-16:]}",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=user_id,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.add(
            RoleBindingRecord(
                id="rb_builder1234567890",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="user",
                principal_id=USER_ID,
                resource_type="workspace",
                resource_id=WORKSPACE_ID,
                role_key="builder",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            RoleBindingRecord(
                id="rb_viewer12345678901",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="user",
                principal_id=VIEWER_ID,
                resource_type="workspace",
                resource_id=WORKSPACE_ID,
                role_key="viewer",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ManagedSecretRecord(
                id=SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="openai_api_key",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )

    service = ModelConfigService(
        sessions,
        built_in_provider_registry(),
        EndpointPolicy(),
        clock=lambda: NOW,
        resolve_dns_on_save=False,
        reference_reader=NoReferences(),
        connection_tester=successful_connection_test,
    )
    try:
        yield service, engine
    finally:
        await engine.dispose()


def actor(user_id: str = USER_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="api_key",
        credential_id="key_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="request-1",
    )


def create_request(*, name: str = "Primary", model_name: str = "gpt-5.6-terra") -> ModelConfigCreate:
    return ModelConfigCreate(
        name=name,
        provider_type="openai",
        model_name=model_name,
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        provider_config={},
    )


@pytest.mark.anyio
async def test_create_commits_model_idempotency_and_safe_audit(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, engine = model_service

    created, replayed = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="create-primary"
    )
    replay, replayed_again = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="create-primary"
    )

    assert not replayed
    assert replayed_again
    assert replay == created
    assert created.id.startswith("mdl_")
    assert created.base_url == "https://api.openai.com/v1"
    assert created.capability_source == "catalog"
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert await session.scalar(select(func.count()).select_from(ModelConfigRecord)) == 1
        audit = await session.scalar(select(SecurityAuditRecord))
        assert audit is not None
        assert audit.action == "model_config.create"
        assert audit.details is None


@pytest.mark.anyio
async def test_idempotency_key_reuse_with_different_input_conflicts(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="same-key")

    with pytest.raises(ModelManagementError) as captured:
        await service.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=create_request(model_name="gpt-5.6-sol"),
            idempotency_key="same-key",
        )

    assert captured.value.code == "idempotency_conflict"
    assert captured.value.status_code == 409


@pytest.mark.anyio
async def test_workspace_name_uniqueness_is_case_insensitive(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(name="Primary"), idempotency_key="one"
    )

    with pytest.raises(ModelManagementError) as captured:
        await service.create(
            actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(name="PRIMARY"), idempotency_key="two"
        )

    assert captured.value.code == "model_name_conflict"


@pytest.mark.anyio
async def test_viewer_can_read_but_cannot_create(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    created, _ = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="create"
    )

    assert (await service.get(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, model_id=created.id)).id == created.id
    with pytest.raises(ModelManagementError) as captured:
        await service.create(
            actor=actor(VIEWER_ID),
            workspace_id=WORKSPACE_ID,
            request=create_request(name="Denied"),
            idempotency_key="denied",
        )
    assert captured.value.code == "resource_not_found"
    assert captured.value.status_code == 404

    unsafe = ModelConfigCreate(
        name="Must Not Resolve",
        provider_type="openai_compatible",
        model_name="local",
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        provider_config={"base_url": "http://127.0.0.1:11434/v1"},
    )
    with pytest.raises(ModelManagementError) as unauthorized:
        await service.create(
            actor=actor(VIEWER_ID),
            workspace_id=WORKSPACE_ID,
            request=unsafe,
            idempotency_key="denied-before-endpoint-validation",
        )
    assert unauthorized.value.code == "resource_not_found"


@pytest.mark.anyio
async def test_missing_secret_reference_fails_closed(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    request = create_request().model_copy(
        update={"credential": WorkspaceSecretCredential(secret_id="sec_abcdef1234567890")}
    )

    with pytest.raises(ModelManagementError) as captured:
        await service.create(
            actor=actor(), workspace_id=WORKSPACE_ID, request=request, idempotency_key="missing-secret"
        )
    assert captured.value.code == "credential_not_eligible"
    assert SECRET_ID not in captured.value.message


@pytest.mark.anyio
async def test_user_secret_requires_exact_invoking_user(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, engine = model_service
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(
            ManagedSecretRecord(
                id="sec_abcdef1234567890",
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="user",
                owner_id=USER_ID,
                key="personal_model_key",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    request = create_request(name="Personal").model_copy(
        update={"credential": InvokingUserSecretCredential(secret_key="personal_model_key")}
    )

    created, _ = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=request, idempotency_key="personal"
    )
    assert created.credential.source == "invoking_user_secret"


@pytest.mark.anyio
async def test_invalid_custom_endpoint_is_rejected_before_persistence(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    request = ModelConfigCreate(
        name="Unsafe",
        provider_type="openai_compatible",
        model_name="local",
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        provider_config={"base_url": "http://127.0.0.1:11434/v1"},
    )

    with pytest.raises(ModelManagementError) as captured:
        await service.create(actor=actor(), workspace_id=WORKSPACE_ID, request=request, idempotency_key="unsafe")
    assert captured.value.code == "invalid_model_configuration"


@pytest.mark.anyio
async def test_list_is_filtered_and_cursor_paginated(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    for index, name in enumerate(("OpenAI Primary", "OpenAI Secondary", "DeepSeek")):
        request = create_request(name=name)
        if name == "DeepSeek":
            request = ModelConfigCreate(
                name=name,
                provider_type="deepseek",
                model_name="deepseek-v4-pro",
                credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
                provider_config={},
                enabled=False,
            )
        await service.create(
            actor=actor(), workspace_id=WORKSPACE_ID, request=request, idempotency_key=f"create-{index}"
        )

    first = await service.list(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, limit=2)
    second = await service.list(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, limit=2, cursor=first.next_cursor)
    filtered = await service.list(
        actor=actor(VIEWER_ID),
        workspace_id=WORKSPACE_ID,
        name="openai",
        provider_type="openai",
        enabled=True,
    )

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert len(second.items) == 1
    assert second.next_cursor is None
    assert {item.name for item in filtered.items} == {"OpenAI Primary", "OpenAI Secondary"}
    with pytest.raises(ModelManagementError, match="cursor"):
        await service.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=3, cursor=first.next_cursor, enabled=True)


@pytest.mark.anyio
async def test_patch_requires_current_etag_and_a_noop_keeps_it(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, engine = model_service
    created, _ = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="create"
    )

    with pytest.raises(ModelManagementError) as captured:
        await service.patch(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            model_id=created.id,
            request=ModelConfigPatch(description="Changed"),
            if_match='"stale"',
        )
    assert captured.value.code == "precondition_failed"
    changed = await service.patch(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=created.id,
        request=ModelConfigPatch(description="Changed", enabled=False),
        if_match=created.strong_etag(),
    )
    noop = await service.patch(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=created.id,
        request=ModelConfigPatch(description="Changed", enabled=False),
        if_match=changed.strong_etag(),
    )

    assert changed.description == "Changed"
    assert not changed.enabled
    assert noop.strong_etag() == changed.strong_etag()
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        audits = tuple((await session.scalars(select(SecurityAuditRecord).order_by(SecurityAuditRecord.action))).all())
    updates = [item for item in audits if item.action == "model_config.update"]
    assert len([item for item in audits if item.action == "model_config.create"]) == 1
    assert len(updates) == 3
    assert [item.outcome for item in updates].count("failure") == 1
    success_details = [item.details for item in updates if item.outcome == "success"]
    assert {"changed_fields": ["description", "enabled"]} in success_details
    assert {"changed_fields": []} in success_details


@pytest.mark.anyio
async def test_copy_reuses_only_non_secret_configuration_and_is_idempotent(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, _ = model_service
    source, _ = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="source"
    )

    copied, replayed = await service.copy(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=source.id,
        request=ModelConfigCopy(name="Copy", description="Copied", enabled=False),
        idempotency_key="copy",
    )
    replay, replayed_again = await service.copy(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        model_id=source.id,
        request=ModelConfigCopy(name="Copy", description="Copied", enabled=False),
        idempotency_key="copy",
    )

    assert not replayed
    assert replayed_again
    assert copied == replay
    assert copied.id != source.id
    assert copied.credential == source.credential
    assert copied.capabilities == source.capabilities
    assert copied.capability_source == source.capability_source
    assert not copied.enabled


@pytest.mark.anyio
async def test_delete_hard_removes_model_but_not_secret(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, engine = model_service
    created, _ = await service.create(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(), idempotency_key="create"
    )

    await service.delete(actor=actor(), workspace_id=WORKSPACE_ID, model_id=created.id, if_match=created.strong_etag())

    with pytest.raises(ModelManagementError) as captured:
        await service.get(actor=actor(), workspace_id=WORKSPACE_ID, model_id=created.id)
    assert captured.value.code == "model_not_found"
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert (
            await session.scalar(select(ManagedSecretRecord.id).where(ManagedSecretRecord.id == SECRET_ID)) == SECRET_ID
        )


@pytest.mark.anyio
async def test_candidate_connection_test_returns_only_safe_bounded_result(
    model_service: tuple[ModelConfigService, AsyncEngine],
) -> None:
    service, engine = model_service

    result = await service.test_candidate(
        actor=actor(), workspace_id=WORKSPACE_ID, request=create_request(name="Unsaved")
    )

    assert result.success
    assert result.code == "connection_succeeded"
    assert result.may_consume_quota_or_incur_cost
    assert SECRET_ID not in result.model_dump_json()
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert await session.scalar(select(func.count()).select_from(ModelConfigRecord)) == 0
        audit = await session.scalar(select(SecurityAuditRecord))
        assert audit is not None and audit.action == "model_config.test"

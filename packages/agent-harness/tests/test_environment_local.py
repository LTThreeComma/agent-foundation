from __future__ import annotations

from pathlib import Path

import pytest
from converge_agent_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    DirectLocalEnvironmentConfiguration,
    DirectLocalEnvironmentProviderBinding,
    DirectLocalFilePolicy,
    DirectLocalRetentionPolicy,
    DirectLocalRootConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentError,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    FileByteRange,
    FileQueryRequest,
    FileRevision,
    OpaqueOutputReference,
    create_environment_run_binding,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema
from pydantic_core import PydanticSerializationError

pytestmark = pytest.mark.anyio


def _instance() -> AgentInstanceContext:
    return AgentInstanceContext(
        identity=AgentIdentityRef(issuer="test", subject="agent"),
        agent_instance_id="agent-1",
    )


def _aggregate(
    root: Path,
    *,
    read_only: bool = False,
    file_policy: DirectLocalFilePolicy | None = None,
):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-1",
            root=DirectLocalRootConfiguration(
                path=root,
                ownership="caller_owned",
                read_only=read_only,
            ),
            files=file_policy or DirectLocalFilePolicy(),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-1",
                binding_revision=1,
                alias="local",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-1",
    )
    return create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


async def test_direct_local_text_revision_patch_copy_and_routing(tmp_path: Path) -> None:
    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        written = await environment.files.write_text(
            "/workspace/note.txt",
            "alpha\nbeta\n",
            mode="create",
        )
        assert isinstance(written.revision, FileRevision)
        page = await environment.files.read_text("note.txt")
        assert page.text == "alpha\nbeta\n"
        assert page.path == "note.txt"

        patched = await environment.files.patch_text(
            "/workspace/note.txt",
            "@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n",
            expected_revision=written.revision,
        )
        assert patched.hunks_applied == 1
        copied = await environment.files.copy(
            "/workspace/note.txt",
            "/workspace/copied.txt",
        )
        assert copied.atomic_destination is True
        assert (tmp_path / "copied.txt").read_text() == "alpha\ngamma\n"

        with pytest.raises(EnvironmentError) as stale:
            await environment.files.write_text(
                "/workspace/note.txt",
                "wrong",
                mode="replace",
                expected_revision=written.revision,
            )
        assert stale.value.code == "environment_conflict"


async def test_direct_local_rejects_escape_symlink_and_read_only_mutation(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-environment.txt"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)

    binding = _aggregate(tmp_path)
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError):
            await environment.files.read_text("/workspace/../outside-environment.txt")
        with pytest.raises(EnvironmentError) as escaped:
            await environment.files.read_text("/workspace/link")
        assert escaped.value.code == "environment_denied"

    read_only = _aggregate(tmp_path, read_only=True)
    async with read_only.bind(run_id="run-2", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as denied:
            await environment.files.write_text("/workspace/new.txt", "x", mode="create")
        assert denied.value.code == "environment_denied"


async def test_binding_owned_root_is_exclusive_and_removed(tmp_path: Path) -> None:
    owned = tmp_path / "owned"
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-owned",
            root=DirectLocalRootConfiguration(path=owned, ownership="binding_owned"),
        )
    )
    request = EnvironmentTopologyRequest(
        topology_version=1,
        bindings=(
            EnvironmentBindingRequest(
                binding_id="binding-owned",
                binding_revision=1,
                alias="owned",
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                default_working_directory="/",
                provider_binding=provider,
            ),
        ),
        default_binding_id="binding-owned",
    )
    binding = create_environment_run_binding(
        initial_topology=request,
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )

    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        await environment.files.write_text("/workspace/value.txt", "value", mode="create")
        assert owned.exists()
    assert not owned.exists()


async def test_raw_transfer_limits_and_uncommitted_writer_abort(tmp_path: Path) -> None:
    (tmp_path / "large.bin").write_bytes(b"12345")
    binding = _aggregate(
        tmp_path,
        file_policy=DirectLocalFilePolicy(
            max_text_bytes=16,
            max_transfer_bytes=4,
            max_query_results=10,
            max_query_bytes=16,
        ),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        with pytest.raises(EnvironmentError) as too_large:
            async with environment.files.open_reader("/workspace/large.bin"):
                pass
        assert too_large.value.code == "environment_too_large"

        async with environment.files.open_reader(
            "/workspace/large.bin",
            byte_range=FileByteRange(offset=1, length=4),
        ) as reader:
            assert b"".join([chunk async for chunk in reader]) == b"2345"

        async with environment.files.open_writer("/workspace/aborted.bin", mode="create") as writer:
            await writer.write(b"data")
        assert not (tmp_path / "aborted.bin").exists()
        assert not tuple(tmp_path.glob(".converge-write-*"))

        with pytest.raises(EnvironmentError) as append_too_large:
            async with environment.files.open_writer("/workspace/large.bin", mode="append"):
                pass
        assert append_too_large.value.code == "environment_too_large"
        assert not tuple(tmp_path.glob(".converge-write-*"))


async def test_query_is_deterministic_bounded_and_cursor_continues(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "1.txt").write_text("1")
    (tmp_path / "a" / "2.txt").write_text("2")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / ".hidden.txt").write_text("hidden")
    binding = _aggregate(
        tmp_path,
        file_policy=DirectLocalFilePolicy(max_query_results=2),
    )
    async with binding.bind(run_id="run-1", instance=_instance()) as environment:
        request = FileQueryRequest(
            root="/workspace",
            pattern="*",
            recursive=True,
            include_hidden=False,
            kinds=frozenset({"file"}),
            max_results=2,
        )
        first = await environment.files.query(request)
        assert [entry.metadata.path for entry in first.entries] == [
            "/workspace/a/1.txt",
            "/workspace/a/2.txt",
        ]
        assert first.next_cursor is not None
        second = await environment.files.query(request.model_copy(update={"cursor": first.next_cursor}))
        assert [entry.metadata.path for entry in second.entries] == ["/workspace/b.txt"]
        assert second.content_complete is True


async def test_local_retention_is_bounded_readable_and_released(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-output",
            root=DirectLocalRootConfiguration(path=tmp_path, ownership="caller_owned"),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        policy = EnvironmentOutputPolicy(max_inline_bytes=4, max_output_bytes=64, overflow="retain")
        capture = await store.capture(b"abcdefghij", policy)
        assert capture.kind == "retained"
        assert capture.reference is not None
        result = await store.read(capture.reference, policy=policy)
        assert result.chunks[0].data == b"abcd"
        assert result.next_cursor is not None
        await store.release(reference=capture.reference)
        with pytest.raises(EnvironmentError):
            await store.read(capture.reference, policy=policy)


async def test_retention_reservations_count_toward_object_quota(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalEnvironmentConfiguration(
            environment_id="local-output-quota",
            root=DirectLocalRootConfiguration(path=tmp_path, ownership="caller_owned"),
            retention=DirectLocalRetentionPolicy(
                max_object_bytes=8,
                max_total_bytes=16,
                max_objects=1,
                max_lifetime_seconds=60,
            ),
        )
    )
    async with provider.bind(
        run_id="run-1",
        instance=_instance(),
        binding_id="binding-1",
        binding_revision=1,
    ) as entered:
        store = entered.operations.outputs
        first = await store.reserve(max_bytes=8)
        with pytest.raises(EnvironmentError) as exhausted:
            await store.reserve(max_bytes=8)
        assert exhausted.value.code == "environment_quota_exceeded"
        await first.abort()
        second = await store.reserve(max_bytes=8)
        await second.abort()


async def test_opaque_output_reference_is_exact_python_only() -> None:
    value = OpaqueOutputReference._from_payload("provider-token")

    class Container(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        reference: OpaqueOutputReference

    model = Container(reference=value)
    assert model.model_dump()["reference"] is value
    assert "provider-token" not in repr(value)
    with pytest.raises(PydanticSerializationError):
        model.model_dump_json()
    with pytest.raises(PydanticSerializationError):
        TypeAdapter(OpaqueOutputReference).dump_json(value)
    with pytest.raises(PydanticInvalidForJsonSchema):
        TypeAdapter(OpaqueOutputReference).json_schema()
    with pytest.raises(ValidationError):
        Container.model_validate({"reference": "provider-token"})

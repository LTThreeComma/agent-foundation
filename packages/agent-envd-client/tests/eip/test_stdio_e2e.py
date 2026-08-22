from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from converge_agent_envd_client import (
    EIPMethodError,
    EIPSession,
    EIPTransportClosedError,
    RequestCoordinator,
    StdioTransport,
)
from converge_agent_envd_client.eip.v1 import (
    DesiredPortStatus,
    EIPCallContext,
    EIPClient,
    EIPClientInfo,
    EIPPath,
    EnvironmentDescribeParams,
    EnvironmentDescribeResult,
    FileListParams,
    FileReadTextParams,
    FileStatParams,
    FileWriteMode,
    InitializeParams,
    MethodSpec,
    OperationCancelParams,
    OperationCancelStatus,
    PortAddress,
    PortInspectParams,
    PortStatus,
    PortTarget,
    PortWaitParams,
    ReceiptGetParams,
    SessionCloseParams,
)


def agent_envd_binary() -> Path:
    configured = os.environ.get("AGENT_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set AGENT_ENVD_TEST_BINARY to run Rust daemon E2E tests")
    binary = Path(configured)
    assert binary.is_file(), f"agent-envd test binary does not exist: {binary}"
    return binary


def assert_disabled_isolation_warning(stderr: bytes) -> None:
    records = [json.loads(line) for line in stderr.splitlines() if line.startswith(b"{")]
    assert any(record.get("event") == "agent-envd.execution_isolation.disabled" for record in records)


async def start_daemon(
    binary: Path,
    environment_id: str = "env-e2e",
    config_path: Path | None = None,
) -> asyncio.subprocess.Process:
    arguments = [str(binary)]
    if config_path is not None:
        arguments.extend(("--config", str(config_path)))
    return await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "AGENT_ENVD_ENVIRONMENT_ID": environment_id,
            "AGENT_ENVD_EXECUTION_ISOLATION": "disabled",
            "AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS": "[ ]",
        },
    )


async def wait_for_exit(process: asyncio.subprocess.Process, expected_code: int = 0) -> bytes:
    try:
        returncode = await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    assert process.stderr is not None
    stderr = await process.stderr.read()
    assert returncode == expected_code, stderr.decode("utf-8", errors="replace")
    return stderr


async def initialize_direct(process: asyncio.subprocess.Process) -> tuple[RequestCoordinator, EIPClient]:
    transport = StdioTransport.from_process(process)
    requester = RequestCoordinator(transport, request_timeout=2)
    client = EIPClient(requester)
    result = await client.initialize(
        InitializeParams(
            supported_protocol_versions=("1.0",),
            client=EIPClientInfo(name="e2e", version="1"),
            expected_environment_id="env-e2e",
        )
    )
    requester.configure_limits(
        max_in_flight=result.descriptor.limits.max_concurrent_operations,
        max_request_bytes=result.descriptor.limits.max_request_bytes,
        max_response_bytes=result.descriptor.limits.max_response_bytes,
        max_transfer_frame_bytes=result.descriptor.limits.max_transfer_frame_bytes,
        max_concurrent_file_transfers=result.descriptor.limits.max_concurrent_file_transfers,
    )
    return requester, client


def test_real_daemon_session_round_trip_and_fresh_generations() -> None:
    async def one_run() -> int:
        process = await start_daemon(agent_envd_binary())
        transport = StdioTransport.from_process(process)
        session = await EIPSession.initialize(
            transport,
            expected_environment_id="env-e2e",
            required_capabilities=("environment.describe", "session.close"),
        )
        descriptor = await session.describe()
        assert descriptor.environment_id == "env-e2e"
        assert descriptor.capabilities == (
            "environment.describe",
            "operation.cancel",
            "port.observe",
            "receipt.read",
            "session.close",
        )

        concurrent = await asyncio.gather(*(session.describe() for _ in range(8)))
        assert all(item.generation == descriptor.generation for item in concurrent)

        listener = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        target = PortTarget(protocol="tcp", address=PortAddress.LOOPBACK, port=port)
        listening = await session.client.port_inspect(
            PortInspectParams(
                context=EIPCallContext(operation_id="port-inspect-e2e"),
                target=target,
            )
        )
        assert listening.observation.status is PortStatus.LISTENING
        listener.close()
        await listener.wait_closed()
        not_listening = await session.client.port_wait(
            PortWaitParams(
                context=EIPCallContext(
                    operation_id="port-wait-e2e",
                    deadline=datetime.now(UTC) + timedelta(seconds=2),
                ),
                target=target,
                desired_status=DesiredPortStatus.NOT_LISTENING,
            )
        )
        assert not_listening.observation.status is PortStatus.NOT_LISTENING

        waiting = asyncio.create_task(
            session.client.port_wait(
                PortWaitParams(
                    context=EIPCallContext(
                        operation_id="port-cancel-target-e2e",
                        deadline=datetime.now(UTC) + timedelta(seconds=5),
                    ),
                    target=target,
                    desired_status=DesiredPortStatus.LISTENING,
                )
            )
        )
        await asyncio.sleep(0.1)
        cancelled = await session.client.operation_cancel(
            OperationCancelParams(
                context=EIPCallContext(operation_id="port-cancel-request-e2e"),
                target_operation_id="port-cancel-target-e2e",
            )
        )
        assert cancelled.status is OperationCancelStatus.CANCELLATION_REQUESTED
        with pytest.raises(EIPMethodError) as cancellation_error:
            await waiting
        assert cancellation_error.value.error.code == -32041
        await session.close()
        assert_disabled_isolation_warning(await wait_for_exit(process))
        return descriptor.generation

    async def scenario() -> None:
        first = await one_run()
        second = await one_run()
        assert first != second

    asyncio.run(scenario())


def test_configured_daemon_resource_and_transfer_plane(tmp_path: Path) -> None:
    native = tmp_path / "native"
    staging = tmp_path / "staging"
    native.mkdir()
    staging.mkdir(mode=0o700)
    config_path = tmp_path / "agent-envd.json"
    config_path.write_text(
        json.dumps(
            {
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(native),
                        "staging_root": str(staging),
                        "writable": True,
                        "exclusive_mutation_control": True,
                        "allow_command_execution": False,
                        "max_file_bytes": 1024 * 1024,
                    }
                ]
            }
        )
    )

    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary(), config_path=config_path)
        session = await EIPSession.initialize(
            StdioTransport.from_process(process),
            expected_environment_id="env-e2e",
            required_capabilities=(
                "file.read",
                "file.write",
                "file.find",
                "file.search",
                "receipt.read",
            ),
            request_timeout=5,
        )
        assert session.descriptor.mounts[0].mount_id == "workspace"
        file_path = EIPPath(mount_id="workspace", path="/binary.dat")
        payload = bytes(range(256)) * 8
        async with session.open_writer(file_path, mode=FileWriteMode.CREATE) as writer:
            await writer.write(payload[:777])
            await writer.write(payload[777:])
            committed = await writer.commit()
        assert committed.transferred_bytes == len(payload)
        assert (native / "binary.dat").read_bytes() == payload

        downloaded = bytearray()
        async with session.open_reader(
            file_path,
            expected_revision=committed.info.revision,
        ) as reader:
            async for chunk in reader:
                downloaded.extend(chunk)
        assert bytes(downloaded) == payload
        assert reader.completion.complete is True

        text_path = EIPPath(mount_id="workspace", path="/notes.txt")
        (native / "notes.txt").write_text("alpha\nbeta\n")
        text = await session.client.file_read_text(
            FileReadTextParams(
                context=EIPCallContext(operation_id="text-e2e"),
                path=text_path,
                max_bytes=64,
            )
        )
        assert text.text == "alpha\nbeta\n"
        assert text.content_complete is True
        stat = await session.client.file_stat(
            FileStatParams(
                context=EIPCallContext(operation_id="stat-e2e"),
                path=text_path,
            )
        )
        assert stat.info.revision == text.info.revision
        listed = await session.client.file_list(
            FileListParams(
                context=EIPCallContext(operation_id="list-e2e"),
                path=EIPPath(mount_id="workspace", path="/"),
            )
        )
        assert [entry.relative_path for entry in listed.entries] == ["binary.dat", "notes.txt"]
        receipt = await session.client.receipt_get(
            ReceiptGetParams(
                context=EIPCallContext(operation_id="receipt-e2e"),
                receipt_ref=committed.receipt.receipt_ref,
            )
        )
        assert receipt.receipt.operation_id == committed.receipt.operation_id
        await session.close()
        assert_disabled_isolation_warning(await wait_for_exit(process))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("expected_environment_id", "required_capabilities"),
    [
        ("wrong-environment", ()),
        ("env-e2e", ("file.read",)),
    ],
)
def test_initialization_negotiation_failure_is_typed_and_terminal(
    expected_environment_id: str,
    required_capabilities: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        transport = StdioTransport.from_process(process)
        with pytest.raises(EIPMethodError) as captured:
            await EIPSession.initialize(
                transport,
                expected_environment_id=expected_environment_id,
                required_capabilities=required_capabilities,
            )
        assert captured.value.error.code == -32003
        assert_disabled_isolation_warning(await wait_for_exit(process))

    asyncio.run(scenario())


def test_incompatible_protocol_version_is_typed_and_terminal() -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        requester = RequestCoordinator(StdioTransport.from_process(process), request_timeout=2)
        client = EIPClient(requester)
        with pytest.raises(EIPMethodError) as captured:
            await client.initialize(
                InitializeParams(
                    supported_protocol_versions=("2.0",),
                    client=EIPClientInfo(name="e2e", version="1"),
                    expected_environment_id="env-e2e",
                )
            )
        assert captured.value.error.code == -32003
        await requester.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_preinitialize_and_repeated_initialize_errors() -> None:
    async def preinitialize() -> None:
        process = await start_daemon(agent_envd_binary())
        requester = RequestCoordinator(StdioTransport.from_process(process), request_timeout=2)
        client = EIPClient(requester)
        with pytest.raises(EIPMethodError) as captured:
            await client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="before-init"))
            )
        assert captured.value.error.code == -32001
        await requester.close()
        await wait_for_exit(process)

    async def repeated() -> None:
        process = await start_daemon(agent_envd_binary())
        requester, client = await initialize_direct(process)
        with pytest.raises(EIPMethodError) as captured:
            await client.initialize(
                InitializeParams(
                    supported_protocol_versions=("1.0",),
                    client=EIPClientInfo(name="e2e", version="1"),
                    expected_environment_id="env-e2e",
                )
            )
        assert captured.value.error.code == -32002
        await client.session_close(SessionCloseParams(context=EIPCallContext(operation_id="close-after-repeat")))
        await requester.close()
        await wait_for_exit(process)

    asyncio.run(preinitialize())
    asyncio.run(repeated())


def test_unknown_method_returns_method_not_found() -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        requester, client = await initialize_direct(process)
        unknown = MethodSpec(
            name="future.unknown",
            capability=None,
            kind="request_response",
            idempotency="read_only_retry",
            idempotency_key="disallowed",
            introduced="1.0",
            error_family="common",
            params_type=EnvironmentDescribeParams,
            result_type=EnvironmentDescribeResult,
        )
        with pytest.raises(EIPMethodError) as captured:
            await requester.request(
                unknown,
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="unknown")),
            )
        assert captured.value.error.code == -32601
        await client.session_close(SessionCloseParams(context=EIPCallContext(operation_id="close-after-unknown")))
        await requester.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


def test_daemon_exit_maps_to_transport_closed() -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        session = await EIPSession.initialize(
            StdioTransport.from_process(process),
            expected_environment_id="env-e2e",
        )
        process.terminate()
        await wait_for_exit(process)
        with pytest.raises(EIPTransportClosedError):
            await session.describe()
        await session.abort()

    asyncio.run(scenario())


def test_raw_invalid_id_gets_nullable_invalid_request_response() -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        assert process.stdin is not None and process.stdout is not None
        body = json.dumps(
            {"jsonrpc": "2.0", "id": True, "method": "initialize", "params": {}},
            separators=(",", ":"),
        ).encode()
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await process.stdin.drain()
        response = await read_raw_frame(process.stdout)
        assert response["id"] is None
        assert response["error"]["code"] == -32600
        process.stdin.close()
        await wait_for_exit(process)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header",
    [
        b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
        b"Content-Length: 1048577\r\n\r\n",
    ],
)
def test_malformed_or_oversized_frame_fails_transport(header: bytes) -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        assert process.stdin is not None
        process.stdin.write(header)
        await process.stdin.drain()
        process.stdin.close()
        stderr = await wait_for_exit(process, expected_code=1)
        assert b"agent-envd failed" in stderr

    asyncio.run(scenario())


def test_sigterm_remains_bounded_when_stdout_is_backpressured() -> None:
    async def scenario() -> None:
        process = await start_daemon(agent_envd_binary())
        assert process.stdin is not None and process.stdout is not None
        initialize = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "supported_protocol_versions": ["1.0"],
                    "client": {"name": "backpressure", "version": "1"},
                    "expected_environment_id": "env-e2e",
                },
            },
            separators=(",", ":"),
        ).encode()
        process.stdin.write(f"Content-Length: {len(initialize)}\r\n\r\n".encode() + initialize)
        await process.stdin.drain()
        assert (await read_raw_frame(process.stdout))["id"] == 1

        frames = bytearray()
        for index in range(2_000):
            body = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": index + 2,
                    "method": "environment.describe",
                    "params": {"context": {"operation_id": f"backpressure-{index}"}},
                },
                separators=(",", ":"),
            ).encode()
            frames.extend(f"Content-Length: {len(body)}\r\n\r\n".encode())
            frames.extend(body)
        process.stdin.write(frames)
        await asyncio.sleep(0.1)
        process.terminate()
        async with asyncio.timeout(5):
            while process.returncode is None:
                await asyncio.sleep(0.01)
        assert process.returncode == 1
        assert process.stderr is not None
        stderr = await process.stderr.read()
        assert_disabled_isolation_warning(stderr)
        assert b"drain exceeded its shutdown deadline" in stderr
        # asyncio's subprocess transport does not finish wait() while an unread
        # stdout pipe remains paused with buffered data, even after child exit.
        await process.stdout.read()
        await process.wait()

    asyncio.run(scenario())


def test_required_isolation_default_fails_closed_before_protocol_admission() -> None:
    async def scenario() -> None:
        process = await asyncio.create_subprocess_exec(
            str(agent_envd_binary()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"AGENT_ENVD_ENVIRONMENT_ID": "env-e2e"},
        )
        stderr = await wait_for_exit(process, expected_code=1)
        assert b"required execution isolation is not available" in stderr
        assert process.stdout is not None
        assert await process.stdout.read() == b""

    asyncio.run(scenario())


def test_parent_eof_before_initialization_and_sigterm_after_readiness_exit_cleanly() -> None:
    async def eof() -> None:
        process = await start_daemon(agent_envd_binary())
        assert process.stdin is not None
        process.stdin.close()
        assert_disabled_isolation_warning(await wait_for_exit(process))

    async def sigterm() -> None:
        process = await start_daemon(agent_envd_binary())
        requester, _ = await initialize_direct(process)
        process.terminate()
        assert_disabled_isolation_warning(await wait_for_exit(process))
        await requester.close()

    asyncio.run(eof())
    asyncio.run(sigterm())


async def read_raw_frame(reader: asyncio.StreamReader) -> dict[str, object]:
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line == b"\r\n":
            break
        assert line
        name, value = line.decode("ascii").split(":", 1)
        headers[name.lower()] = value.strip()
    body = await reader.readexactly(int(headers["content-length"]))
    value = json.loads(body)
    assert isinstance(value, dict)
    return value

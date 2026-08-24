from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import pytest
from converge_agent_envd_client import ControlFrame, EIPSession, EIPTransportFrame, RequestCoordinator
from converge_agent_envd_client.eip.v1 import (
    ContentDigest,
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    EIPLimits,
    EIPPath,
    EnvironmentDescriptor,
    FileByteRange,
    FileInfo,
    FileKind,
    FileReadCompletion,
    FileReaderCloseParams,
    FileReaderCloseResult,
    FileReaderHandle,
    FileReaderOpenResult,
    FileWriteMode,
    FileWriterAbortParams,
    FileWriterAbortResult,
    FileWriterAbortStatus,
    FileWriterCommitParams,
    FileWriterCommitResult,
    FileWriterHandle,
    FileWriterOpenResult,
    IsolationBackend,
    IsolationCleanupGuarantee,
    IsolationMode,
    IsolationNetworkPolicy,
    IsolationPosture,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    OperationReceipt,
    ReceiptOutcome,
    ReceiptRef,
    ReceiptStage,
    ResourceAuthority,
    ResourceAuthorityDescriptor,
    decode_model,
    encode_model,
)
from converge_agent_envd_client.errors import EIPProtocolError
from pydantic import BaseModel


class FakeTypedTransport:
    def __init__(self) -> None:
        self.outbound: asyncio.Queue[EIPTransportFrame] = asyncio.Queue()
        self.inbound: asyncio.Queue[EIPTransportFrame] = asyncio.Queue()
        self.closed = False

    async def send(self, frame: EIPTransportFrame) -> None:
        await self.outbound.put(frame)

    async def receive(self) -> EIPTransportFrame:
        return await self.inbound.get()

    async def close(self) -> None:
        self.closed = True

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None:
        return None


async def next_control(transport: FakeTypedTransport, method: str) -> JsonRpcRequest:
    frame = await transport.outbound.get()
    assert isinstance(frame, ControlFrame)
    request = decode_model(frame.payload, JsonRpcRequest)
    assert request.method == method
    return request


async def respond(
    transport: FakeTypedTransport,
    request: JsonRpcRequest,
    result: BaseModel,
) -> None:
    await transport.inbound.put(
        ControlFrame(
            encode_model(
                JsonRpcSuccessResponse(
                    jsonrpc="2.0",
                    id=request.id,
                    result=json.loads(encode_model(result)),
                )
            )
        )
    )


def decode_params(request: JsonRpcRequest, model_type: type[BaseModel]) -> Any:
    return decode_model(json.dumps(request.params).encode(), model_type)


def descriptor() -> EnvironmentDescriptor:
    return EnvironmentDescriptor(
        environment_id="env-transfer",
        generation=1,
        capabilities=("file.read", "file.write"),
        limits=EIPLimits(
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_concurrent_operations=4,
            max_processes=1,
            max_operation_duration_ms=1000,
            max_inline_output_bytes=1,
            max_output_bytes=1,
            max_retained_bytes=1,
            max_retained_objects=1,
            max_retention_ttl_ms=1,
            max_operation_records=8,
            operation_record_ttl_ms=1,
            session_idle_ttl_ms=1000,
            max_process_records=1,
            terminal_process_record_ttl_ms=1,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
            max_file_transfer_records=2,
            file_transfer_record_ttl_ms=1,
            max_staged_file_bytes=1024,
            max_staged_file_objects=2,
            file_transfer_idle_ttl_ms=1000,
            max_file_transfer_duration_ms=1000,
        ),
        resource_authority=ResourceAuthorityDescriptor(mode=ResourceAuthority.SCOPED),
        isolation=IsolationPosture(
            mode=IsolationMode.DISABLED,
            backend=IsolationBackend.OUTER_HOST,
            filesystem_containment=False,
            process_containment=False,
            network_containment=False,
            network_policy=IsolationNetworkPolicy.HOST,
            cleanup_guarantee=IsolationCleanupGuarantee.OUTER_HOST,
        ),
    )


def info(path: EIPPath, size: int) -> FileInfo:
    return FileInfo(
        path=path,
        kind=FileKind.FILE,
        size_bytes=size,
        executable=False,
    )


def receipt(operation_id: str) -> OperationReceipt:
    return OperationReceipt(
        receipt_ref=ReceiptRef("receipt-transfer"),
        operation_id=operation_id,
        method="file.commit_writer",
        environment_id="env-transfer",
        generation=1,
        request_digest="0" * 64,
        stage=ReceiptStage.COMPLETED,
        outcome=ReceiptOutcome.SUCCEEDED,
        observed_at="2026-08-21T00:00:00Z",
    )


def test_high_level_reader_withholds_ack_until_iteration_drains_and_verifies() -> None:
    async def scenario() -> None:
        transport = FakeTypedTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        requester.configure_limits(
            max_in_flight=4,
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
        )
        session = EIPSession(requester, descriptor())
        path = EIPPath(mount_id="workspace", path="/source.bin")
        content = b"first-second"

        async def peer() -> None:
            opened = await next_control(transport, "file.open_reader")
            await respond(
                transport,
                opened,
                FileReaderOpenResult(
                    reader=FileReaderHandle("reader-one"),
                    info=info(path, len(content)),
                    expires_at="2026-08-21T01:00:00Z",
                ),
            )
            attach = await transport.outbound.get()
            assert isinstance(attach, DataFrame) and attach.kind is DataFrameKind.ATTACH
            await transport.inbound.put(DataFrame(kind=DataFrameKind.ATTACHED, handle="reader-one"))
            await transport.inbound.put(DataFrame(kind=DataFrameKind.CHUNK, handle="reader-one", payload=b"first-"))
            await transport.inbound.put(
                DataFrame(kind=DataFrameKind.CHUNK, handle="reader-one", offset=6, payload=b"second")
            )
            await transport.inbound.put(DataFrame(kind=DataFrameKind.END, handle="reader-one", offset=len(content)))
            ack = await transport.outbound.get()
            assert isinstance(ack, DataFrame)
            assert ack.kind is DataFrameKind.END_ACK and ack.offset == len(content)
            closed = await next_control(transport, "file.close_reader")
            close_params = decode_params(closed, FileReaderCloseParams)
            assert isinstance(close_params, FileReaderCloseParams) and close_params.accept_complete
            await respond(
                transport,
                closed,
                FileReaderCloseResult(
                    completion=FileReadCompletion(
                        produced_bytes=len(content),
                        digest=ContentDigest(
                            algorithm="sha256",
                            value=hashlib.sha256(content).hexdigest(),
                        ),
                        complete=True,
                    )
                ),
            )

        peer_task = asyncio.create_task(peer())
        async with session.open_reader(path) as reader:
            chunks = [chunk async for chunk in reader]
            assert b"".join(chunks) == content
            assert reader.completion.complete
        await peer_task
        await session.abort()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("byte_range", "reported_size", "payload"),
    [
        (FileByteRange(offset=0, length=0), 1, b"x"),
        (FileByteRange(offset=0, length=3), 4, b"four"),
        (None, 0, b"x"),
    ],
)
def test_high_level_reader_rejects_bytes_beyond_requested_maximum(
    byte_range: FileByteRange | None,
    reported_size: int,
    payload: bytes,
) -> None:
    async def scenario() -> None:
        transport = FakeTypedTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        requester.configure_limits(
            max_in_flight=4,
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
        )
        session = EIPSession(requester, descriptor())
        path = EIPPath(mount_id="workspace", path="/source.bin")

        async def peer() -> None:
            opened = await next_control(transport, "file.open_reader")
            await respond(
                transport,
                opened,
                FileReaderOpenResult(
                    reader=FileReaderHandle("reader-excess"),
                    info=info(path, reported_size),
                    expires_at="2026-08-21T01:00:00Z",
                ),
            )
            attach = await transport.outbound.get()
            assert isinstance(attach, DataFrame) and attach.kind is DataFrameKind.ATTACH
            await transport.inbound.put(DataFrame(kind=DataFrameKind.ATTACHED, handle="reader-excess"))
            await transport.inbound.put(DataFrame(kind=DataFrameKind.CHUNK, handle="reader-excess", payload=payload))

            protocol_reset = await transport.outbound.get()
            assert isinstance(protocol_reset, DataFrame)
            assert protocol_reset.kind is DataFrameKind.RESET
            assert protocol_reset.reset_status is DataResetStatus.PROTOCOL
            cancellation_reset = await transport.outbound.get()
            assert isinstance(cancellation_reset, DataFrame)
            assert cancellation_reset.kind is DataFrameKind.RESET
            closed = await next_control(transport, "file.close_reader")
            close_params = decode_params(closed, FileReaderCloseParams)
            assert isinstance(close_params, FileReaderCloseParams)
            assert not close_params.accept_complete
            await respond(
                transport,
                closed,
                FileReaderCloseResult(
                    completion=FileReadCompletion(
                        produced_bytes=0,
                        digest=None,
                        complete=False,
                    )
                ),
            )

        peer_task = asyncio.create_task(peer())
        with pytest.raises(EIPProtocolError, match="requested maximum"):
            async with session.open_reader(path, byte_range=byte_range) as reader:
                await anext(reader)
        await peer_task
        await session.abort()

    asyncio.run(scenario())


def test_repeated_writer_abandonment_consumes_reset_acknowledgements() -> None:
    async def scenario() -> None:
        transport = FakeTypedTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        requester.configure_limits(
            max_in_flight=4,
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
        )
        session = EIPSession(requester, descriptor())
        path = EIPPath(mount_id="workspace", path="/abandoned.bin")

        async def peer() -> None:
            for index in range(6):
                opened = await next_control(transport, "file.open_writer")
                handle = f"writer-{index}"
                await respond(
                    transport,
                    opened,
                    FileWriterOpenResult(
                        writer=FileWriterHandle(handle),
                        max_transfer_bytes=1024,
                        expires_at="2026-08-21T01:00:00Z",
                    ),
                )
                attach = await transport.outbound.get()
                assert isinstance(attach, DataFrame) and attach.kind is DataFrameKind.ATTACH
                await transport.inbound.put(DataFrame(kind=DataFrameKind.ATTACHED, handle=handle))
                reset = await transport.outbound.get()
                assert isinstance(reset, DataFrame) and reset.kind is DataFrameKind.RESET
                await transport.inbound.put(
                    DataFrame(
                        kind=DataFrameKind.RESET,
                        handle=handle,
                        offset=reset.offset,
                        reset_status=reset.reset_status,
                    )
                )
                aborted = await next_control(transport, "file.abort_writer")
                params = decode_params(aborted, FileWriterAbortParams)
                assert isinstance(params, FileWriterAbortParams)
                await respond(
                    transport,
                    aborted,
                    FileWriterAbortResult(status=FileWriterAbortStatus.ABORTED),
                )

        peer_task = asyncio.create_task(peer())
        for _ in range(6):
            async with session.open_writer(path, mode=FileWriteMode.CREATE):
                pass
        await peer_task
        assert not requester._retired_transfers
        await session.abort()

    asyncio.run(scenario())


def test_queued_peer_reader_reset_does_not_consume_retired_capacity() -> None:
    async def scenario() -> None:
        transport = FakeTypedTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        requester.configure_limits(
            max_in_flight=4,
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
        )
        session = EIPSession(requester, descriptor())
        path = EIPPath(mount_id="workspace", path="/reset.bin")
        reset_delivered = [asyncio.Event() for _ in range(6)]

        async def peer() -> None:
            for index, delivered in enumerate(reset_delivered):
                opened = await next_control(transport, "file.open_reader")
                handle = f"reader-reset-{index}"
                await respond(
                    transport,
                    opened,
                    FileReaderOpenResult(
                        reader=FileReaderHandle(handle),
                        info=info(path, 0),
                        expires_at="2026-08-21T01:00:00Z",
                    ),
                )
                attach = await transport.outbound.get()
                assert isinstance(attach, DataFrame) and attach.kind is DataFrameKind.ATTACH
                await transport.inbound.put(DataFrame(kind=DataFrameKind.ATTACHED, handle=handle))
                await transport.inbound.put(
                    DataFrame(
                        kind=DataFrameKind.RESET,
                        handle=handle,
                        reset_status=DataResetStatus.SOURCE,
                    )
                )
                while not requester._transfers[handle].peer_reset_received:
                    await asyncio.sleep(0)
                delivered.set()

                closed = await next_control(transport, "file.close_reader")
                close_params = decode_params(closed, FileReaderCloseParams)
                assert isinstance(close_params, FileReaderCloseParams)
                assert not close_params.accept_complete
                await respond(
                    transport,
                    closed,
                    FileReaderCloseResult(
                        completion=FileReadCompletion(
                            produced_bytes=0,
                            digest=None,
                            complete=False,
                        )
                    ),
                )

        peer_task = asyncio.create_task(peer())
        for delivered in reset_delivered:
            async with session.open_reader(path):
                await delivered.wait()
        await peer_task
        assert not requester._retired_transfers
        await session.abort()

    asyncio.run(scenario())


def test_high_level_writer_frames_chunks_and_commits_local_digest() -> None:
    async def scenario() -> None:
        transport = FakeTypedTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        requester.configure_limits(
            max_in_flight=4,
            max_request_bytes=1024 * 1024,
            max_response_bytes=1024 * 1024,
            max_transfer_frame_bytes=128,
            max_concurrent_file_transfers=2,
        )
        session = EIPSession(requester, descriptor())
        path = EIPPath(mount_id="workspace", path="/target.bin")
        content = b"uploaded-content"

        async def peer() -> None:
            opened = await next_control(transport, "file.open_writer")
            await respond(
                transport,
                opened,
                FileWriterOpenResult(
                    writer=FileWriterHandle("writer-one"),
                    max_transfer_bytes=1024,
                    expires_at="2026-08-21T01:00:00Z",
                ),
            )
            attach = await transport.outbound.get()
            assert isinstance(attach, DataFrame) and attach.kind is DataFrameKind.ATTACH
            await transport.inbound.put(DataFrame(kind=DataFrameKind.ATTACHED, handle="writer-one"))

            received = bytearray()
            while True:
                frame = await transport.outbound.get()
                assert isinstance(frame, DataFrame)
                if frame.kind is DataFrameKind.END:
                    assert frame.offset == len(received)
                    await transport.inbound.put(
                        DataFrame(kind=DataFrameKind.END_ACK, handle="writer-one", offset=len(received))
                    )
                    break
                assert frame.kind is DataFrameKind.CHUNK
                assert frame.offset == len(received)
                received.extend(frame.payload)
            assert bytes(received) == content

            committed = await next_control(transport, "file.commit_writer")
            params = decode_params(committed, FileWriterCommitParams)
            assert isinstance(params, FileWriterCommitParams)
            assert params.transferred_bytes == len(content)
            assert params.transfer_digest.value == hashlib.sha256(content).hexdigest()
            await respond(
                transport,
                committed,
                FileWriterCommitResult(
                    info=info(path, len(content)),
                    transferred_bytes=len(content),
                    transfer_digest=params.transfer_digest,
                    receipt=receipt(params.context.operation_id),
                ),
            )

        peer_task = asyncio.create_task(peer())
        async with session.open_writer(path, mode=FileWriteMode.CREATE) as writer:
            await writer.write(content)
            result = await writer.commit()
            assert result.transferred_bytes == len(content)
        await peer_task
        await session.abort()

    asyncio.run(scenario())

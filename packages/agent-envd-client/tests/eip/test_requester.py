from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import converge_agent_envd_client.requester as requester_module
import pytest
from converge_agent_envd_client import (
    ControlFrame,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPSession,
    EIPSessionStateError,
    EIPTransportFrame,
    RequestCoordinator,
)
from converge_agent_envd_client.eip.v1 import (
    DataFrame,
    DataFrameKind,
    DataResetStatus,
    DispatchStage,
    EIPCallContext,
    EIPClient,
    EIPError,
    EIPErrorData,
    EIPLimits,
    EnvironmentDescribeParams,
    EnvironmentDescribeResult,
    EnvironmentDescriptor,
    ErrorType,
    IsolationBackend,
    IsolationCleanupGuarantee,
    IsolationMode,
    IsolationNetworkPolicy,
    IsolationPosture,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    ResourceAuthority,
    ResourceAuthorityDescriptor,
    RetryHint,
    decode_model,
    encode_model,
)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: asyncio.Queue[EIPTransportFrame] = asyncio.Queue()
        self.responses: asyncio.Queue[EIPTransportFrame | bytes | BaseException] = asyncio.Queue()
        self.closed = False
        self.limits: tuple[int, int, int] | None = None

    async def send(self, frame: EIPTransportFrame) -> None:
        await self.sent.put(frame)

    async def receive(self) -> EIPTransportFrame:
        response = await self.responses.get()
        if isinstance(response, BaseException):
            raise response
        return ControlFrame(response) if isinstance(response, bytes) else response

    async def close(self) -> None:
        self.closed = True

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None:
        self.limits = (max_request_bytes, max_response_bytes, max_transfer_frame_bytes)


class BlockingCloseTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.allow_close.wait()
        self.closed = True


def decode_sent_request(frame: EIPTransportFrame) -> JsonRpcRequest:
    assert isinstance(frame, ControlFrame)
    return decode_model(frame.payload, JsonRpcRequest)


def descriptor(generation: int) -> EnvironmentDescriptor:
    return EnvironmentDescriptor(
        environment_id="env-test",
        generation=generation,
        capabilities=("environment.describe", "session.close"),
        limits=EIPLimits(
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_concurrent_operations=4,
            max_processes=1,
            max_operation_duration_ms=1000,
            max_inline_output_bytes=1,
            max_output_bytes=1,
            max_retained_bytes=1,
            max_retained_objects=1,
            max_retention_ttl_ms=1,
            max_operation_records=4,
            operation_record_ttl_ms=1,
            session_idle_ttl_ms=1000,
            max_process_records=1,
            terminal_process_record_ttl_ms=1,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
            max_file_transfer_records=1,
            file_transfer_record_ttl_ms=1,
            max_staged_file_bytes=1,
            max_staged_file_objects=1,
            file_transfer_idle_ttl_ms=1,
            max_file_transfer_duration_ms=1,
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


def success_response(
    request_id: str | int,
    generation: int,
    descriptor_value: EnvironmentDescriptor | None = None,
) -> bytes:
    result = EnvironmentDescribeResult(descriptor=descriptor_value or descriptor(generation))
    return encode_model(
        JsonRpcSuccessResponse(
            jsonrpc="2.0",
            id=request_id,
            result=json.loads(encode_model(result)),
        )
    )


def test_request_coordinator_correlates_out_of_order_responses() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=2, request_timeout=1)
        client = EIPClient(requester)
        first = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        second = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="second")))
        )

        first_request = decode_sent_request(await transport.sent.get())
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 2))
        await transport.responses.put(success_response(first_request.id, 1))

        first_result, second_result = await asyncio.gather(first, second)
        assert first_result.descriptor.generation == 1
        assert second_result.descriptor.generation == 2
        await requester.close()

    asyncio.run(scenario())


def test_request_coordinator_raises_typed_method_error() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        client = EIPClient(requester)
        call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="describe")))
        )
        request = decode_sent_request(await transport.sent.get())
        error = EIPError(
            code=-32012,
            message="unsupported",
            data=EIPErrorData(
                error_type=ErrorType.UNSUPPORTED,
                retry_hint=RetryHint.NEVER,
                dispatch_stage=DispatchStage.PRE_DISPATCH,
                capability="environment.describe",
            ),
        )
        await transport.responses.put(encode_model(JsonRpcErrorResponse(jsonrpc="2.0", id=request.id, error=error)))

        with pytest.raises(EIPMethodError) as captured:
            await call
        assert captured.value.error is error or captured.value.error == error
        await requester.close()

    asyncio.run(scenario())


def test_unknown_response_id_terminates_requester() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        client = EIPClient(requester)
        call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="describe")))
        )
        await transport.sent.get()
        await transport.responses.put(success_response(999, 1))

        with pytest.raises(EIPProtocolError, match="unknown or duplicate"):
            await call
        assert transport.closed
        await requester.close()

    asyncio.run(scenario())


def test_cancelled_wait_keeps_correlation_until_late_response() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=None)
        client = EIPClient(requester)
        cancelled = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="cancelled")))
        )
        first_request = decode_sent_request(await transport.sent.get())
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        next_call = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="next")))
        )
        await asyncio.sleep(0)
        assert transport.sent.empty()

        await transport.responses.put(success_response(first_request.id, 1))
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 2))
        assert (await next_call).descriptor.generation == 2
        await requester.close()

    asyncio.run(scenario())


def test_deadline_covers_waiting_for_admission() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=None)
        client = EIPClient(requester)
        first = asyncio.create_task(
            client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="first")))
        )
        first_request = decode_sent_request(await transport.sent.get())

        deadline = datetime.now(UTC) + timedelta(milliseconds=20)
        with pytest.raises(EIPRequestTimeoutError) as captured:
            await client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="deadline", deadline=deadline))
            )
        assert captured.value.dispatched is False
        assert transport.sent.empty()

        await transport.responses.put(success_response(first_request.id, 1))
        assert (await first).descriptor.generation == 1
        await requester.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("delayed_encoding", [1, 2])
def test_deadline_expired_during_encoding_is_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
    delayed_encoding: int,
) -> None:
    original_encode_model = requester_module.encode_model
    encode_count = 0

    def delayed_encode_model(model: Any) -> bytes:
        nonlocal encode_count
        encode_count += 1
        if encode_count == delayed_encoding:
            time.sleep(0.01)
        return original_encode_model(model)

    monkeypatch.setattr(requester_module, "encode_model", delayed_encode_model)

    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=0.001)
        client = EIPClient(requester)

        with pytest.raises(EIPRequestTimeoutError) as captured:
            await client.environment_describe(EnvironmentDescribeParams(context=EIPCallContext(operation_id="expired")))
        assert captured.value.dispatched is False
        assert transport.sent.empty()
        await requester.close()

    asyncio.run(scenario())


def test_requester_close_finishes_cleanup_before_propagating_repeated_cancellation() -> None:
    async def scenario() -> None:
        transport = BlockingCloseTransport()
        requester = RequestCoordinator(transport)
        close = asyncio.create_task(requester.close())
        await transport.close_started.wait()
        close.cancel()
        await asyncio.sleep(0)
        close.cancel()
        await asyncio.sleep(0)
        assert not close.done()

        transport.allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert transport.closed
        await requester.close()

    asyncio.run(scenario())


def test_descriptor_refresh_narrows_limits_and_identity_violation_is_terminal() -> None:
    async def narrowing() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=4, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        narrowed_limits = descriptor(1).limits.model_copy(
            update={
                "max_request_bytes": 512,
                "max_response_bytes": 512,
                "max_concurrent_operations": 1,
                "max_operation_records": 1,
            }
        )
        narrowed = descriptor(1).model_copy(update={"limits": narrowed_limits})
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 1, narrowed))
        assert (await refresh).limits.max_concurrent_operations == 1
        assert transport.limits == (512, 512, 1024)

        first = asyncio.create_task(
            session.client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="after-narrow-1"))
            )
        )
        second = asyncio.create_task(
            session.client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="after-narrow-2"))
            )
        )
        first_request = decode_sent_request(await transport.sent.get())
        await asyncio.sleep(0)
        assert transport.sent.empty()
        await transport.responses.put(success_response(first_request.id, 1, narrowed))
        await first
        second_request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(second_request.id, 1, narrowed))
        await second
        await session.abort()

    async def identity_violation() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, request_timeout=1)
        session = EIPSession(requester, descriptor(1))
        refresh = asyncio.create_task(session.describe())
        request = decode_sent_request(await transport.sent.get())
        await transport.responses.put(success_response(request.id, 2))
        with pytest.raises(EIPProtocolError, match="generation changed"):
            await refresh
        assert transport.closed
        with pytest.raises(EIPSessionStateError):
            _ = session.client

    asyncio.run(narrowing())
    asyncio.run(identity_violation())


def test_session_rejects_invalid_local_admission_before_initialize() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        with pytest.raises(ValueError, match="max_in_flight"):
            await EIPSession.initialize(
                transport,
                expected_environment_id="env-test",
                max_in_flight=0,
            )
        assert transport.sent.empty()
        assert not transport.closed

    asyncio.run(scenario())


def test_request_coordinator_demultiplexes_data_without_blocking_control() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport, max_in_flight=1, request_timeout=1)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-test", inbound_frames=2)
        call = asyncio.create_task(
            EIPClient(requester).environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id="interleaved"))
            )
        )
        request = decode_sent_request(await transport.sent.get())

        attached = DataFrame(kind=DataFrameKind.ATTACHED, handle=channel.handle)
        await transport.responses.put(attached)
        await transport.responses.put(success_response(request.id, 7))

        assert await channel.receive() == attached
        assert (await call).descriptor.generation == 7
        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_slow_transfer_consumer_applies_backpressure_without_reset() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-full", inbound_frames=1)
        first = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"a")
        second = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, offset=1, payload=b"b")
        await transport.responses.put(first)
        await transport.responses.put(second)
        await asyncio.sleep(0)

        assert transport.sent.empty()
        assert await channel.receive() == first
        assert await channel.receive() == second
        assert transport.sent.empty()

        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_peer_reset_bypasses_a_full_transfer_inbox() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-reset", inbound_frames=1)
        chunk = DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"queued")
        reset = DataFrame(
            kind=DataFrameKind.RESET,
            handle=channel.handle,
            reset_status=DataResetStatus.SOURCE,
        )
        await channel.deliver(chunk)
        await asyncio.wait_for(channel.deliver(reset), timeout=1)
        assert channel.peer_reset_received
        channel.fail(RuntimeError("carrier also failed"))
        assert await channel.receive() == chunk
        assert await channel.receive() == reset

        requester.unregister_transfer(channel)
        await requester.close()

    asyncio.run(scenario())


def test_retired_transfer_ignores_already_queued_terminal_frames() -> None:
    async def scenario() -> None:
        transport = FakeTransport()
        requester = RequestCoordinator(transport)
        requester.configure_limits(
            max_in_flight=1,
            max_request_bytes=1024,
            max_response_bytes=1024,
            max_transfer_frame_bytes=1024,
            max_concurrent_file_transfers=1,
        )
        channel = requester.register_transfer("reader-retired", inbound_frames=1)
        await channel.deliver(DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"queued"))
        blocked_delivery = asyncio.create_task(
            channel.deliver(
                DataFrame(
                    kind=DataFrameKind.CHUNK,
                    handle=channel.handle,
                    offset=6,
                    payload=b"blocked",
                )
            )
        )
        await asyncio.sleep(0)
        assert not blocked_delivery.done()
        requester.retire_transfer(channel)
        await asyncio.wait_for(blocked_delivery, timeout=1)
        await transport.responses.put(DataFrame(kind=DataFrameKind.CHUNK, handle=channel.handle, payload=b"late"))
        await transport.responses.put(DataFrame(kind=DataFrameKind.END, handle=channel.handle, offset=6))
        await transport.responses.put(DataFrame(kind=DataFrameKind.END_ACK, handle=channel.handle, offset=6))
        await asyncio.sleep(0)
        assert channel.handle in requester._retired_transfers
        assert requester._terminal_error is None
        await transport.responses.put(
            DataFrame(
                kind=DataFrameKind.RESET,
                handle=channel.handle,
                reset_status=DataResetStatus.CANCELLED,
            )
        )
        await asyncio.sleep(0)
        assert channel.handle not in requester._retired_transfers
        assert requester._terminal_error is None
        await requester.close()

    asyncio.run(scenario())

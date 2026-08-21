from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel

from converge_agent_envd_client.eip.v1 import (
    EIPRequester,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    MethodSpec,
    decode_model,
    encode_model,
)
from converge_agent_envd_client.errors import (
    EIPClientError,
    EIPMethodError,
    EIPProtocolError,
    EIPRequestTimeoutError,
    EIPTransportClosedError,
    EIPTransportError,
)
from converge_agent_envd_client.transport import EIPTransport

_MAX_JSONRPC_ID = 2**63 - 1
_RESPONSE_TYPE = cast(
    type[JsonRpcSuccessResponse | JsonRpcErrorResponse],
    JsonRpcSuccessResponse | JsonRpcErrorResponse,
)


@dataclass(slots=True)
class _PendingRequest:
    method: MethodSpec[Any, Any]
    future: asyncio.Future[object]
    abandoned: bool = False


class _AdmissionLimiter:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._waiters: set[asyncio.Future[None]] = set()

    async def acquire(self) -> None:
        while self._active >= self._limit:
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.add(waiter)
            try:
                await waiter
            finally:
                self._waiters.discard(waiter)
        self._active += 1

    def release(self) -> None:
        if self._active < 1:
            raise RuntimeError("EIP admission release without an active request")
        self._active -= 1
        self._wake_waiters()

    def set_limit(self, limit: int) -> None:
        self._limit = limit
        self._wake_waiters()

    def narrow_limit(self, limit: int) -> None:
        self._limit = min(self._limit, limit)

    def _wake_waiters(self) -> None:
        for waiter in tuple(self._waiters):
            if not waiter.done():
                waiter.set_result(None)


class RequestCoordinator(EIPRequester):
    """Correlates bounded concurrent generated EIP requests without retrying them."""

    def __init__(
        self,
        transport: EIPTransport,
        *,
        max_in_flight: int = 32,
        request_timeout: float | None = None,
    ) -> None:
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        if request_timeout is not None and request_timeout <= 0:
            raise ValueError("request_timeout must be positive or None")
        self._transport = transport
        self._admission = _AdmissionLimiter(max_in_flight)
        self._request_timeout = request_timeout
        self._pending: dict[str | int, _PendingRequest] = {}
        self._next_id = 1
        self._reader_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closed = False
        self._terminal_error: BaseException | None = None

    async def request[P, R](self, method: MethodSpec[P, R], params: P) -> R:
        if self._closed or self._terminal_error is not None:
            raise EIPTransportClosedError("EIP requester is closed") from self._terminal_error
        if not isinstance(params, method.params_type):
            raise TypeError(f"{method.name} params must be {method.params_type.__name__}")

        request_timeout = _effective_timeout(params, self._request_timeout)
        if request_timeout is not None and request_timeout <= 0:
            raise EIPRequestTimeoutError("EIP request deadline expired before dispatch", dispatched=False)
        loop = asyncio.get_running_loop()
        timeout_at = loop.time() + request_timeout if request_timeout is not None else None

        params_payload = json.loads(encode_model(cast(BaseModel, params)))
        if not isinstance(params_payload, dict):
            raise EIPProtocolError("generated EIP params did not encode as an object")

        try:
            await _wait_until(self._admission.acquire(), timeout_at)
        except TimeoutError as error:
            raise EIPRequestTimeoutError(
                "timed out waiting for EIP admission before dispatch",
                dispatched=False,
            ) from error
        if _deadline_expired(timeout_at):
            self._admission.release()
            raise EIPRequestTimeoutError(
                "EIP request deadline expired before dispatch",
                dispatched=False,
            )
        if self._closed or self._terminal_error is not None:
            self._admission.release()
            raise EIPTransportClosedError("EIP requester is closed") from self._terminal_error

        request_id = self._allocate_id()
        envelope = JsonRpcRequest(jsonrpc="2.0", id=request_id, method=method.name, params=params_payload)
        payload = encode_model(envelope)
        if _deadline_expired(timeout_at):
            self._admission.release()
            raise EIPRequestTimeoutError(
                "EIP request deadline expired before dispatch",
                dispatched=False,
            )

        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        pending = _PendingRequest(cast(MethodSpec[Any, Any], method), future)
        self._pending[request_id] = pending
        self._ensure_reader()

        try:
            await _wait_until(self._transport.send(payload), timeout_at)
        except TimeoutError as error:
            pending.abandoned = True
            raise EIPRequestTimeoutError(
                "timed out sending an EIP request; operation outcome is not implied",
                dispatched=True,
            ) from error
        except asyncio.CancelledError:
            pending.abandoned = True
            raise
        except Exception as error:
            pending.abandoned = True
            transport_error = (
                error if isinstance(error, EIPClientError) else EIPTransportError("failed to send EIP request")
            )
            self._terminate(transport_error)
            await self._transport.close()
            raise transport_error from error

        try:
            return cast(R, await _wait_until(asyncio.shield(future), timeout_at))
        except TimeoutError as error:
            pending.abandoned = True
            raise EIPRequestTimeoutError(
                "timed out waiting for an EIP response; operation outcome is not implied",
                dispatched=True,
            ) from error
        except asyncio.CancelledError:
            pending.abandoned = True
            raise

    def configure_limits(
        self,
        *,
        max_in_flight: int,
        max_request_bytes: int,
        max_response_bytes: int,
    ) -> None:
        if self._pending:
            raise RuntimeError("cannot reconfigure requester limits with in-flight requests")
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        self._admission.set_limit(max_in_flight)
        self._transport.set_limits(
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )

    def narrow_limits(
        self,
        *,
        max_in_flight: int,
        max_request_bytes: int,
        max_response_bytes: int,
    ) -> None:
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        self._admission.narrow_limit(max_in_flight)
        self._transport.set_limits(
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close(), name="eip-requester-close")
        close_task = self._close_task
        await _await_shared_close(close_task)

    async def _close(self) -> None:
        reader_task = self._reader_task
        if reader_task is not None:
            reader_task.cancel()
        try:
            await self._transport.close()
        finally:
            if reader_task is not None:
                await asyncio.gather(reader_task, return_exceptions=True)
            self._fail_pending(EIPTransportClosedError("EIP requester closed"))

    def _ensure_reader(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._reader_loop(), name="eip-response-reader")

    def _allocate_id(self) -> int:
        for _ in range(len(self._pending) + 1):
            request_id = self._next_id
            self._next_id = 1 if request_id == _MAX_JSONRPC_ID else request_id + 1
            if request_id not in self._pending:
                return request_id
        raise RuntimeError("no JSON-RPC request ID is available")

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                payload = await self._transport.receive()
                response = decode_model(payload, _RESPONSE_TYPE)
                if response.id is None:
                    raise EIPProtocolError("received an uncorrelated JSON-RPC error response")
                pending = self._pending.pop(response.id, None)
                if pending is None:
                    raise EIPProtocolError("received an unknown or duplicate JSON-RPC response ID")
                self._admission.release()

                if isinstance(response, JsonRpcErrorResponse):
                    if not pending.abandoned:
                        pending.future.set_exception(EIPMethodError(response.error))
                    continue

                try:
                    result_payload = json.dumps(
                        response.result,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                    result = decode_model(result_payload, pending.method.result_type)
                except Exception as error:
                    protocol_error = EIPProtocolError(f"invalid {pending.method.name} result from EIP peer")
                    if not pending.abandoned:
                        pending.future.set_exception(protocol_error)
                    raise protocol_error from error
                if not pending.abandoned:
                    pending.future.set_result(result)
        except asyncio.CancelledError:
            return
        except Exception as error:
            terminal_error = error if isinstance(error, EIPClientError) else EIPProtocolError("invalid EIP response")
            self._terminate(terminal_error)
            await self._transport.close()

    def _terminate(self, error: BaseException) -> None:
        if self._terminal_error is None:
            self._terminal_error = error
        self._fail_pending(error)

    def _fail_pending(self, error: BaseException) -> None:
        pending_requests = tuple(self._pending.values())
        self._pending.clear()
        for pending in pending_requests:
            self._admission.release()
            if not pending.abandoned and not pending.future.done():
                pending.future.set_exception(error)


async def _await_shared_close(close_task: asyncio.Task[None]) -> None:
    cancelled = False
    while True:
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            if close_task.done():
                raise
            cancelled = True
            continue
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        break
    if cancelled:
        raise asyncio.CancelledError


async def _wait_until[T](awaitable: Awaitable[T], timeout_at: float | None) -> T:
    if timeout_at is None:
        return await awaitable
    async with asyncio.timeout_at(timeout_at):
        return await awaitable


def _deadline_expired(timeout_at: float | None) -> bool:
    return timeout_at is not None and asyncio.get_running_loop().time() >= timeout_at


def _effective_timeout(params: object, configured_timeout: float | None) -> float | None:
    context = getattr(params, "context", None)
    deadline = getattr(context, "deadline", None)
    if deadline is None:
        return configured_timeout
    parsed_deadline = datetime.fromisoformat(deadline.removesuffix("Z") + "+00:00")
    remaining = (parsed_deadline - datetime.now(UTC)).total_seconds()
    return remaining if configured_timeout is None else min(configured_timeout, remaining)

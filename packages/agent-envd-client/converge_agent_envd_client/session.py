from __future__ import annotations

import asyncio
import secrets
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from types import TracebackType
from typing import Never

from converge_agent_envd_client.eip.v1 import (
    EIP_PROTOCOL_VERSION,
    EIPCallContext,
    EIPClient,
    EIPClientInfo,
    EIPPath,
    EnvironmentDescribeParams,
    EnvironmentDescriptor,
    FileByteRange,
    FileRevision,
    FileWriteMode,
    InitializeParams,
    SessionCloseParams,
)
from converge_agent_envd_client.errors import EIPProtocolError, EIPSessionStateError
from converge_agent_envd_client.file_transfer import EIPFileReader, EIPFileWriter
from converge_agent_envd_client.requester import RequestCoordinator
from converge_agent_envd_client.transport import EIPTransport


class EIPSession:
    """One initialized EIP session over a low-level transport."""

    def __init__(
        self,
        requester: RequestCoordinator,
        descriptor: EnvironmentDescriptor,
    ) -> None:
        self._requester = requester
        self._client = EIPClient(requester)
        self._descriptor = descriptor
        self._describe_lock = asyncio.Lock()
        self._closed = False

    @classmethod
    async def initialize(
        cls,
        transport: EIPTransport,
        *,
        expected_environment_id: str,
        required_capabilities: tuple[str, ...] = (),
        optional_capabilities: tuple[str, ...] = (),
        client_name: str = "converge-agent-envd-client",
        client_version: str | None = None,
        initialization_timeout: float = 10.0,
        request_timeout: float | None = None,
        max_in_flight: int = 32,
    ) -> EIPSession:
        if initialization_timeout <= 0:
            raise ValueError("initialization_timeout must be positive")
        if not isinstance(max_in_flight, int) or isinstance(max_in_flight, bool) or max_in_flight < 1:
            raise ValueError("max_in_flight must be a positive integer")
        requester = RequestCoordinator(
            transport,
            max_in_flight=1,
            request_timeout=request_timeout,
        )
        client = EIPClient(requester)
        params = InitializeParams(
            supported_protocol_versions=(EIP_PROTOCOL_VERSION,),
            client=EIPClientInfo(
                name=client_name,
                version=client_version or _distribution_version(),
            ),
            expected_environment_id=expected_environment_id,
            required_capabilities=required_capabilities,
            optional_capabilities=optional_capabilities,
        )
        try:
            async with asyncio.timeout(initialization_timeout):
                result = await client.initialize(params)
            if result.protocol_version != EIP_PROTOCOL_VERSION:
                raise EIPProtocolError("server selected an unoffered EIP protocol version")
            descriptor = result.descriptor
            if descriptor.environment_id != expected_environment_id:
                raise EIPProtocolError("server returned a different Environment identity")
            missing = sorted(set(required_capabilities) - set(descriptor.capabilities))
            if missing:
                raise EIPProtocolError(f"server omitted required capability: {missing[0]}")
            requester.configure_limits(
                max_in_flight=min(max_in_flight, descriptor.limits.max_concurrent_operations),
                max_request_bytes=descriptor.limits.max_request_bytes,
                max_response_bytes=descriptor.limits.max_response_bytes,
                max_transfer_frame_bytes=descriptor.limits.max_transfer_frame_bytes,
                max_concurrent_file_transfers=descriptor.limits.max_concurrent_file_transfers,
            )
            return cls(requester, descriptor)
        except BaseException:
            await requester.close()
            raise

    @property
    def client(self) -> EIPClient:
        """The generated typed client bound to this initialized session."""
        self._ensure_open()
        return self._client

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self._descriptor

    @property
    def generation(self) -> int:
        return self._descriptor.generation

    def open_reader(
        self,
        path: EIPPath,
        *,
        byte_range: FileByteRange | None = None,
        expected_revision: FileRevision | None = None,
        transfer_deadline: datetime | None = None,
    ) -> EIPFileReader:
        self._ensure_open()
        self._require_capability("file.read")
        return EIPFileReader(
            self._requester,
            self._client,
            path,
            byte_range=byte_range,
            expected_revision=expected_revision,
            transfer_deadline=transfer_deadline,
        )

    def open_writer(
        self,
        path: EIPPath,
        *,
        mode: FileWriteMode | str,
        expected_revision: FileRevision | None = None,
        executable: bool | None = None,
        transfer_deadline: datetime | None = None,
    ) -> EIPFileWriter:
        self._ensure_open()
        self._require_capability("file.write")
        resolved_mode = mode if isinstance(mode, FileWriteMode) else FileWriteMode(mode)
        return EIPFileWriter(
            self._requester,
            self._client,
            path,
            resolved_mode,
            expected_revision=expected_revision,
            executable=executable,
            transfer_deadline=transfer_deadline,
            max_transfer_frame_bytes=self._descriptor.limits.max_transfer_frame_bytes,
        )

    async def describe(self) -> EnvironmentDescriptor:
        self._ensure_open()
        async with self._describe_lock:
            self._ensure_open()
            result = await self._client.environment_describe(
                EnvironmentDescribeParams(context=EIPCallContext(operation_id=_operation_id()))
            )
            descriptor = result.descriptor
            if descriptor.environment_id != self._descriptor.environment_id:
                await self._terminate_protocol_error(
                    EIPProtocolError("Environment identity changed within an EIP session")
                )
            if descriptor.generation != self._descriptor.generation:
                await self._terminate_protocol_error(
                    EIPProtocolError("Environment generation changed within an EIP session")
                )
            self._requester.narrow_limits(
                max_in_flight=descriptor.limits.max_concurrent_operations,
                max_request_bytes=descriptor.limits.max_request_bytes,
                max_response_bytes=descriptor.limits.max_response_bytes,
                max_transfer_frame_bytes=descriptor.limits.max_transfer_frame_bytes,
                max_concurrent_file_transfers=descriptor.limits.max_concurrent_file_transfers,
            )
            self._descriptor = descriptor
            return descriptor

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.session_close(SessionCloseParams(context=EIPCallContext(operation_id=_operation_id())))
        finally:
            await self._requester.close()

    async def abort(self) -> None:
        """Close the carrier without claiming an in-flight operation outcome."""
        if self._closed:
            return
        self._closed = True
        await self._requester.close()

    async def __aenter__(self) -> EIPSession:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exception_type is None:
            await self.close()
            return
        try:
            await self.close()
        except BaseException:
            pass

    async def _terminate_protocol_error(self, error: EIPProtocolError) -> Never:
        self._closed = True
        await self._requester.close()
        raise error

    def _ensure_open(self) -> None:
        if self._closed:
            raise EIPSessionStateError("EIP session is closed")

    def _require_capability(self, capability: str) -> None:
        if capability not in self._descriptor.capabilities:
            raise EIPSessionStateError(f"EIP capability is not available: {capability}")


def _operation_id() -> str:
    return f"op-{secrets.token_urlsafe(9)}"


def _distribution_version() -> str:
    try:
        return version("converge-agent-envd-client")
    except PackageNotFoundError:
        return "0.0.0"

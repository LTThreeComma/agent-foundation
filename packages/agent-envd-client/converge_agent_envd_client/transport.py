from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from converge_agent_envd_client.eip.v1 import DataFrame


@dataclass(frozen=True, slots=True)
class ControlFrame:
    payload: bytes


EIPTransportFrame = ControlFrame | DataFrame


class EIPTransport(Protocol):
    async def send(self, frame: EIPTransportFrame) -> None: ...

    async def receive(self) -> EIPTransportFrame: ...

    async def close(self) -> None: ...

    def set_limits(
        self,
        *,
        max_request_bytes: int,
        max_response_bytes: int,
        max_transfer_frame_bytes: int,
    ) -> None: ...

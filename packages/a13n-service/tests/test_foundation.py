"""The assembled control process serves authenticated requests over the template schema."""

import pytest

pytestmark = pytest.mark.anyio


async def test_control_process_is_ready_and_authenticates(service) -> None:  # type: ignore[no-untyped-def]
    assert (await service.client.get("/healthz")).json()["role"] == "control"
    ready = await service.client.get("/readyz")
    assert ready.status_code == 200, ready.text
    me = await service.client.get("/api/v1/users/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "admin@example.com"

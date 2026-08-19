import asyncio

import httpx2
from converge_foundation_service.app import create_app
from converge_foundation_service.settings import ServiceRole, ServiceSettings


def test_health_reports_process_role() -> None:
    settings = ServiceSettings(role=ServiceRole.execution)

    async def request_health() -> httpx2.Response:
        transport = httpx2.ASGITransport(app=create_app(settings))
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/healthz")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "execution"}

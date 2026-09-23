"""Control always recovers expired OAuth claims, independently of token keep-alive."""

import asyncio

from a13n_logging import get_logger

from a13n_service.infra.db import Storage
from a13n_service.resources.connections.oauth_tokens import recover_deadlines
from a13n_service.settings import OAuth

logger = get_logger(__name__)


async def maintain_authorizations(storage: Storage, settings: OAuth) -> None:
    while True:
        try:
            async with asyncio.timeout(5):
                await recover_deadlines(storage)
        except Exception as error:
            logger.warning("OAuth maintenance interrupted", extra={"error_type": type(error).__name__})
        await asyncio.sleep(settings.scan_seconds)

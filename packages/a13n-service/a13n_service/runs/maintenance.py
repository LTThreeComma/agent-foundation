"""Control-owned bounded scans over idle threads and expired attempts."""

import asyncio

from a13n_logging import get_logger
from redis.asyncio import Redis
from sqlalchemy import func, select

from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.runs import seal, wakeups
from a13n_service.runs.advance import advance_one
from a13n_service.runs.policy import AdmissionPolicy
from a13n_service.runs.tables import AttemptRow
from a13n_service.settings import Settings

logger = get_logger(__name__)


async def maintain(storage: Storage, redis: Redis, *, config: Settings, policy: AdmissionPolicy | None) -> None:
    keys = KeyRing(active_key_id=config.encryption.active_key_id, keys=config.encryption.keys)
    while True:
        try:
            async with asyncio.timeout(30):
                async with short_session(storage) as session:
                    run_ids = (
                        await session.scalars(
                            select(AttemptRow.run_id)
                            .where(
                                AttemptRow.status.in_(("leased", "running")),
                                AttemptRow.lease_expires_at <= func.clock_timestamp(),
                            )
                            .order_by(AttemptRow.lease_expires_at, AttemptRow.id)
                            .limit(32)
                        )
                    ).all()
                for run_id in run_ids:
                    if await seal.expire(storage, run_id):
                        await wakeups.notify(redis, timeout=config.redis.timeout)
                for _ in range(32):
                    if not await advance_one(
                        storage, max_attempts=config.worker.max_attempts, keys=keys, policy=policy
                    ):
                        break
                    await wakeups.notify(redis, timeout=config.redis.timeout)
        except Exception as error:
            logger.warning("Run maintenance interrupted", extra={"error_type": type(error).__name__})
        await asyncio.sleep(config.control.scan_seconds)

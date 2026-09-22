"""Immutable, deduplicated usage facts accepted from known attempts, including late reports."""

import hashlib
import hmac
import json
from datetime import datetime

from a13n_harness.usage import ModelUsageRecord
from a13n_logging import get_logger
from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, ForeignKey, ForeignKeyConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.tables import ModelRow
from a13n_service.runs.schemas import AttemptClaim
from a13n_service.runs.tables import AttemptRow, RunRow
from a13n_service.tenancy.authenticate import secret_hash
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.grants import workspace_scope

logger = get_logger(__name__)


class UsageRow(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
        ForeignKeyConstraint(["run_id", "run_attempt_id"], ["run_attempts.run_id", "run_attempts.id"]),
    )
    id: Mapped[str] = mapped_column(primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str]
    run_id: Mapped[str]
    run_attempt_id: Mapped[str]
    harness_run_id: Mapped[str]
    call_id: Mapped[str | None]
    digest: Mapped[str]
    record: Mapped[dict] = mapped_column(JSONB)
    model_id: Mapped[str | None] = mapped_column(ForeignKey("models.id"))
    price_snapshot: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


async def ingest(
    storage: Storage,
    claim: AttemptClaim,
    record: ModelUsageRecord,
    *,
    model_id: str | None,
    price_snapshot: dict | None,
) -> None:
    value = record.model_dump(mode="json")
    identity = {
        "record": value,
        "run_id": claim.run_id,
        "run_attempt_id": claim.attempt_id,
        "model_id": model_id,
        "price_snapshot": price_snapshot,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 65536:
        raise ServiceError("payload_too_large", "Usage record exceeds its byte limit")
    digest = hashlib.sha256(encoded).hexdigest()
    async with transaction(storage) as session:
        attempt = await session.get(AttemptRow, claim.attempt_id)
        if (
            attempt is None
            or attempt.run_id != claim.run_id
            or attempt.organization_id != claim.organization_id
            or attempt.workspace_id != claim.workspace_id
            or attempt.worker_id != claim.worker_id
            or not hmac.compare_digest(attempt.lease_token_hash, secret_hash(claim.token))
            or attempt.harness_run_id != record.run_id
        ):
            raise ServiceError("forbidden", "Usage report does not belong to this attempt")
        if model_id is not None:
            model = await session.get(ModelRow, model_id)
            if (
                model is None
                or model.organization_id != claim.organization_id
                or model.workspace_id not in {None, claim.workspace_id}
            ):
                raise ServiceError("forbidden", "Usage model is outside the attempt scope")
        await session.execute(
            insert(UsageRow)
            .values(
                id=record.record_id,
                organization_id=claim.organization_id,
                workspace_id=claim.workspace_id,
                run_id=claim.run_id,
                run_attempt_id=claim.attempt_id,
                harness_run_id=record.run_id,
                call_id=record.call_id,
                digest=digest,
                record=value,
                model_id=model_id,
                price_snapshot=price_snapshot,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        existing = (await session.execute(select(UsageRow.digest).where(UsageRow.id == record.record_id))).scalar_one()
        if existing != digest:
            logger.error(
                "Usage record integrity conflict", extra={"run_id": claim.run_id, "record_id": record.record_id}
            )
            raise ServiceError(
                "conflict", "Usage record ID has different immutable content", {"record_id": record.record_id}
            )


async def totals(session: AsyncSession, run_id: str) -> dict[str, int]:
    requests, input_tokens, output_tokens = (
        await session.execute(
            select(
                func.count(UsageRow.id),
                func.coalesce(func.sum(UsageRow.record["request_usage"]["input_tokens"].astext.cast(BigInteger)), 0),
                func.coalesce(func.sum(UsageRow.record["request_usage"]["output_tokens"].astext.cast(BigInteger)), 0),
            ).where(UsageRow.run_id == run_id, UsageRow.call_id.is_not(None))
        )
    ).one()
    return {"requests": int(requests), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens)}


class UsageView(BaseModel):
    run_id: str
    current: dict[str, int]
    at_seal: dict[str, int] | None


async def view(storage: Storage, actor: Principal, workspace_id: str, run_id: str) -> UsageView:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assert scope.workspace_id is not None
        workspace_id = scope.workspace_id
        run = await session.get(RunRow, run_id)
        if run is None or run.workspace_id != workspace_id:
            raise ServiceError("not_found", "Run was not found")
        return UsageView(run_id=run.id, current=await totals(session, run.id), at_seal=run.usage_at_seal)

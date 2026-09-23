"""Immutable usage facts: ingestion from known attempts, including late reports, and run totals.

Ingestion is not fenced by the worker lease: it records a past charge, so an expired or finished attempt
may still report. It is scoped instead: the record must belong to this run's attempt and tenant.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from a13n_harness.usage import ModelUsageRecord, ProviderUsageRecord
from a13n_logging import get_logger
from pydantic import JsonValue
from sqlalchemy import BigInteger, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow

logger = get_logger(__name__)

MAX_RECORD_BYTES = 65536


@dataclass(frozen=True, slots=True)
class UsageReport:
    """One Harness usage record with the dispatch identity the Service established before sending it."""

    record: ModelUsageRecord | ProviderUsageRecord
    model_id: str | None = None
    # None records an unknown price; displayed historical cost never re-reads current pricing.
    price_snapshot: dict[str, JsonValue] | None = None


def _values(run: RunRow, attempt: AttemptRow, report: UsageReport) -> dict:
    record = report.record.model_dump(mode="json")
    digest = hashlib.sha256(
        canonical_json([record, run.id, attempt.id, report.model_id, report.price_snapshot])
    ).hexdigest()
    if len(canonical_json(record)) > MAX_RECORD_BYTES:
        raise ServiceError("payload_too_large", "Usage record exceeds its byte limit", {"limit": MAX_RECORD_BYTES})
    return {
        "id": report.record.record_id,
        "organization_id": run.organization_id,
        "workspace_id": run.workspace_id,
        "run_id": run.id,
        "run_attempt_id": attempt.id,
        "harness_run_id": report.record.run_id,
        "call_id": getattr(report.record, "call_id", None),
        "digest": digest,
        "record": record,
        "model_id": report.model_id,
        "price_snapshot": report.price_snapshot,
    }


async def ingest(session: AsyncSession, run: RunRow, attempt: AttemptRow, reports: Sequence[UsageReport]) -> None:
    """Insert each record once. A duplicate ID with equal content is a no-op; different content is refused."""
    for report in reports:
        if attempt.run_id != run.id or report.record.run_id != attempt.harness_run_id:
            raise ServiceError("forbidden", "Usage report does not belong to this attempt")
        values = _values(run, attempt, report)
        await session.execute(insert(UsageRecordRow).values(**values).on_conflict_do_nothing(index_elements=["id"]))
        stored = await session.scalar(select(UsageRecordRow.digest).where(UsageRecordRow.id == values["id"]))
        if stored != values["digest"]:
            logger.error("Usage record integrity conflict", extra={"run_id": run.id, "record_id": values["id"]})
            raise ServiceError("conflict", "Usage record ID has different immutable content", {"id": values["id"]})


async def totals(session: AsyncSession, run_id: str) -> dict[str, int]:
    """Model request count and tokens recorded so far; `requests` is what `max_usage` limits."""
    usage = UsageRecordRow.record["request_usage"]
    requests, input_tokens, output_tokens = (
        await session.execute(
            select(
                func.count(UsageRecordRow.id),
                func.coalesce(func.sum(usage["input_tokens"].astext.cast(BigInteger)), 0),
                func.coalesce(func.sum(usage["output_tokens"].astext.cast(BigInteger)), 0),
            ).where(UsageRecordRow.run_id == run_id, UsageRecordRow.record["kind"].astext == "model")
        )
    ).one()
    return {"requests": int(requests), "input_tokens": int(input_tokens), "output_tokens": int(output_tokens)}

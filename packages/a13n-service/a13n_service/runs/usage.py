"""Immutable usage facts: ingestion from known attempts, including late reports, and run totals.

Ingestion is not fenced by the worker lease: it records a past charge, so an expired or finished attempt
may still report. It is scoped instead to the run's attempt and tenant. Records keep the Harness run that
made them: the attempt's own, or an inline subagent's whose events the attempt's stream forwards.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import anyio
from a13n_harness.errors import RunError
from a13n_harness.usage import (
    ModelUsageRecord,
    ProviderUsageRecord,
    UsageRecord,
    validate_price_policy,
    validate_revision,
)
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, advisory_lock, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow

MAX_RECORD_BYTES = 65536
_RECORD = TypeAdapter(UsageRecord)


@dataclass(frozen=True, slots=True)
class UsageReport:
    """One Harness usage record with the dispatch identity the Service established before sending it."""

    record: ModelUsageRecord | ProviderUsageRecord
    model_id: str | None = None
    # None records an unknown price; displayed historical cost never re-reads current pricing.
    price_snapshot: dict[str, JsonValue] | None = None


def _values(run: RunRow, attempt: AttemptRow, report: UsageReport, record: dict) -> dict:
    digest = hashlib.sha256(
        canonical_json([record, run.id, attempt.id, report.model_id, report.price_snapshot])
    ).hexdigest()
    return {
        "id": report.record.record_id,
        "revision": report.record.revision,
        "cost": report.record.request_usage.cost
        if isinstance(report.record, ModelUsageRecord)
        else report.record.usage.cost,
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
    """Persist immutable, tenant-scoped versions. Conflicting delivery fails explicitly."""
    if attempt.run_id != run.id:
        raise ServiceError("forbidden", "Usage report does not belong to this attempt")
    rows: dict[tuple[str, int], dict] = {}
    for report in reports:
        record = report.record.model_dump(mode="json")
        if len(canonical_json(record)) > MAX_RECORD_BYTES:
            raise ServiceError("payload_too_large", "Usage record exceeds its byte limit")
        key = (report.record.record_id, report.record.revision)
        candidate = _values(run, attempt, report, record)
        if key in rows and rows[key] != candidate:
            raise ServiceError("conflict", "Conflicting usage record revision")
        rows[key] = candidate
    if not rows:
        return
    # Serialize only receipt ingestion, never model execution. Sort keys to avoid deadlocks.
    for record_id in sorted({key[0] for key in rows}):
        await advisory_lock(session, "usage", run.workspace_id, record_id)
    # Record identity comes from the collector; later versions retain the first Host owner.
    previous = (
        (
            await session.execute(
                select(UsageRecordRow.__table__)
                .where(
                    UsageRecordRow.workspace_id == run.workspace_id,
                    UsageRecordRow.id.in_({key[0] for key in rows}),
                )
                .distinct(UsageRecordRow.id)
                .order_by(UsageRecordRow.id, UsageRecordRow.revision)
            )
        )
        .mappings()
        .all()
    )
    owners = {row["id"]: dict(row) for row in previous}
    for row in sorted(rows.values(), key=lambda value: value["revision"]):
        owner = owners.get(row["id"])
        if owner is None:
            owners[row["id"]] = row
            continue
        if row["model_id"] != owner["model_id"]:
            raise ServiceError("conflict", "Usage record changed its selected model")
        try:
            record = validate_revision(_RECORD.validate_python(owner["record"]), _RECORD.validate_python(row["record"]))
            validate_price_policy(owner["price_snapshot"], row["price_snapshot"], row["cost"])
        except RunError as error:
            raise ServiceError("conflict", str(error)) from error
        row["record"] = record.model_dump(mode="json")
        for name in ("run_id", "run_attempt_id", "harness_run_id", "call_id", "price_snapshot"):
            row[name] = owner[name]
        row["digest"] = hashlib.sha256(
            canonical_json(
                [row["record"], row["run_id"], row["run_attempt_id"], row["model_id"], row["price_snapshot"]]
            )
        ).hexdigest()
    await session.execute(
        insert(UsageRecordRow)
        .values(list(rows.values()))
        .on_conflict_do_nothing(index_elements=["workspace_id", "id", "revision"])
    )
    stored = (
        await session.execute(
            select(UsageRecordRow.id, UsageRecordRow.revision, UsageRecordRow.digest).where(
                UsageRecordRow.workspace_id == run.workspace_id,
                tuple_(UsageRecordRow.id, UsageRecordRow.revision).in_(rows),
            )
        )
    ).all()
    if any(rows[(record_id, revision)]["digest"] != digest for record_id, revision, digest in stored):
        raise ServiceError("conflict", "Conflicting usage record revision")


async def persist(storage: Storage, run_id: str, attempt_id: str, reports: Sequence[UsageReport]) -> None:
    """Persist received facts in a short transaction, independent of worker lease state."""
    if not reports:
        return
    async with transaction(storage) as session:
        pair = (
            await session.execute(
                select(RunRow, AttemptRow)
                .join(
                    AttemptRow,
                    AttemptRow.run_id == RunRow.id,
                )
                .where(RunRow.id == run_id, AttemptRow.id == attempt_id)
            )
        ).one_or_none()
        if pair is None:
            raise ServiceError("not_found", "Usage report names no attempt")
        await ingest(session, pair[0], pair[1], reports)


def price_snapshot(model: ResolvedModel) -> dict[str, JsonValue] | None:
    return model.pricing.model_dump(mode="json") if model.pricing is not None else None


class DatabaseUsageReporter:
    """The Service's only normal ingestion entry point, independent of checkpoints and leases."""

    def __init__(self, storage: Storage, run_id: str, attempt_id: str, models: Mapping[str, ResolvedModel]):
        self.storage, self.run_id, self.attempt_id = storage, run_id, attempt_id
        self.models = models

    async def report(self, records: tuple[UsageRecord, ...]) -> None:
        reports = []
        for record in records:
            model = self.models.get(record.model_id or "") if isinstance(record, ModelUsageRecord) else None
            reports.append(UsageReport(record, model.id if model else None, price_snapshot(model) if model else None))
        for attempt in range(3):
            try:
                await persist(self.storage, self.run_id, self.attempt_id, reports)
                return
            except OperationalError:
                if attempt == 2:
                    raise
                await anyio.sleep(0.05 * 2**attempt)

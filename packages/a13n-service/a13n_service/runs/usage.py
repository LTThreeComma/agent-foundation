"""Immutable usage facts: ingestion from known attempts, including late reports, and run totals.

Ingestion is not fenced by the worker lease: it records a past charge, so an expired or finished attempt
may still report. It is scoped instead to the run's attempt and tenant. Records keep the Harness run that
made them: the attempt's own, or an inline subagent's whose events the attempt's stream forwards.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from a13n_harness.usage import ModelUsageRecord, ProviderUsageRecord, UsageRecord
from pydantic import JsonValue
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, advisory_lock, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.service import ResolvedModel
from a13n_service.runs.schemas import canonical_json
from a13n_service.runs.tables import AttemptRow, RunRow, UsageRecordRow

if TYPE_CHECKING:
    from a13n_service.runs.calls import CallCheck

MAX_RECORD_BYTES = 65536


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


async def ingest(
    session: AsyncSession, run: RunRow, attempt: AttemptRow, reports: Sequence[UsageReport]
) -> frozenset[str]:
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
        return frozenset()
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
        if (
            row["record"]["kind"] == "provider"
            and owner["record"]["kind"] == "provider"
            and row["revision"] == owner["revision"]
            and row["record"]["usage"] == owner["record"]["usage"]
        ):
            row["record"] = owner["record"]
        if row["model_id"] != owner["model_id"]:
            raise ServiceError("conflict", "Usage record changed its selected model")
        for name in (
            "kind",
            "run_id",
            "call_id",
            "request_started_at",
            "provider_response_id",
            "response_ordinal",
            "ordinal",
            "agent_instance_id",
            "parent_agent_instance_id",
            "delegation_id",
            "source",
            "tool_id",
            "tool_call_id",
        ):
            if name in owner["record"] and row["record"].get(name) != owner["record"][name]:
                raise ServiceError("conflict", "Usage record changed its attribution")
        if row["price_snapshot"] != owner["price_snapshot"] and row["cost"] is not None:
            raise ServiceError("conflict", "Usage revision changed its price policy")
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

    return frozenset(row["id"] for row in rows.values() if row["run_attempt_id"] == attempt.id)


async def persist(storage: Storage, run_id: str, attempt_id: str, reports: Sequence[UsageReport]) -> frozenset[str]:
    """Persist received facts in a short transaction, independent of worker lease state."""
    if not reports:
        return frozenset()
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
        return await ingest(session, pair[0], pair[1], reports)


def price_snapshot(model: ResolvedModel) -> dict[str, JsonValue] | None:
    return model.pricing.model_dump(mode="json") if model.pricing is not None else None


class DatabaseUsageReporter:
    """The Service's only normal ingestion entry point, independent of checkpoints and leases."""

    def __init__(self, storage: Storage, run_id: str, attempt_id: str, check: "CallCheck"):
        self.storage, self.run_id, self.attempt_id = storage, run_id, attempt_id
        self.check = check

    async def report(self, records: tuple[UsageRecord, ...]) -> None:
        reports = []
        for record in records:
            model = self.check.models.get(record.model_id or "") if isinstance(record, ModelUsageRecord) else None
            reports.append(UsageReport(record, model.id if model else None, price_snapshot(model) if model else None))
        for attempt in range(3):
            try:
                owned = await persist(self.storage, self.run_id, self.attempt_id, reports)
                self.check.recorded(records, owned)
                return
            except OperationalError:
                if attempt == 2:
                    raise
                await anyio.sleep(0.05 * 2**attempt)

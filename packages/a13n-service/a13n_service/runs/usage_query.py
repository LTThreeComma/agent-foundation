"""Database projections of the latest usage facts and explicit delegation scopes."""

from a13n_harness.money import sum_decimal
from a13n_harness.usage import TOKEN_COUNTERS
from sqlalchemy import BigInteger, ColumnElement, Numeric, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, transaction
from a13n_service.runs.schemas import ModelUsage, ProviderCost, UsageFilter, UsageSummary
from a13n_service.runs.tables import RunRow, ThreadRow, UsageRecordRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def _runs(workspace_id: str, where: UsageFilter) -> Select:
    query = select(RunRow.id).where(RunRow.workspace_id == workspace_id)
    if where.run_id is not None:
        if where.scope == "tree":
            tree = query.where(RunRow.id == where.run_id).cte("usage_tree", recursive=True)
            tree = tree.union(
                select(RunRow.id)
                .join(ThreadRow, ThreadRow.id == RunRow.thread_id)
                .join(
                    tree,
                    tree.c.id == ThreadRow.origin_run_id,
                )
                .where(ThreadRow.origin == "child", RunRow.workspace_id == workspace_id)
            )
            query = query.where(RunRow.id.in_(select(tree.c.id)))
        else:
            query = query.where(RunRow.id == where.run_id)
    for column, value in ((RunRow.thread_id, where.thread_id), (RunRow.session_id, where.session_id)):
        if value is not None:
            query = query.where(column == value)
    return query


def _latest(workspace_id: str, runs: Select):
    return (
        select(UsageRecordRow)
        .where(UsageRecordRow.workspace_id == workspace_id, UsageRecordRow.run_id.in_(runs))
        .distinct(
            UsageRecordRow.id,
        )
        .order_by(UsageRecordRow.id, UsageRecordRow.revision.desc())
        .subquery()
    )


async def totals(session: AsyncSession, run_id: str) -> dict[str, int]:
    # Version selection precedes aggregation; polling a background job is not a new request.
    latest = (
        select(UsageRecordRow)
        .where(UsageRecordRow.run_id == run_id)
        .distinct(
            UsageRecordRow.id,
        )
        .order_by(UsageRecordRow.id, UsageRecordRow.revision.desc())
        .subquery()
    )
    values = (
        await session.execute(
            select(
                func.count(),
                *(
                    func.coalesce(func.sum(latest.c.record["request_usage"][name].astext.cast(BigInteger)), 0)
                    for name in ("input_tokens", "output_tokens")
                ),
            ).where(latest.c.record["kind"].astext == "model")
        )
    ).one()
    return dict(zip(("requests", "input_tokens", "output_tokens"), map(int, values), strict=True))


async def summarize(storage: Storage, actor: Principal, workspace_id: str, where: UsageFilter) -> UsageSummary:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        runs = _runs(scope.workspace_id, where)
        latest = _latest(scope.workspace_id, runs)
        predicates: list[ColumnElement[bool]] = [latest.c.run_id.in_(runs)]
        if where.ingested_after is not None:
            predicates.append(latest.c.ingested_at >= where.ingested_after)
        if where.ingested_before is not None:
            predicates.append(latest.c.ingested_at < where.ingested_before)
        usage = latest.c.record["request_usage"]
        model_rows = (
            (
                await session.execute(
                    select(
                        latest.c.model_id,
                        func.count().label("requests"),
                        *(
                            func.coalesce(func.sum(usage[name].astext.cast(BigInteger)), 0).label(name)
                            for name in TOKEN_COUNTERS
                        ),
                        func.coalesce(func.sum(usage["audio_seconds"].astext.cast(Numeric)), 0).label("audio_seconds"),
                        func.sum(latest.c.cost).label("cost"),
                        func.count().filter(latest.c.cost.is_(None)).label("unknown_cost_records"),
                        func.count()
                        .filter(latest.c.record["usage_status"].astext != "complete")
                        .label("incomplete_requests"),
                    )
                    .where(*predicates, latest.c.record["kind"].astext == "model")
                    .group_by(latest.c.model_id)
                    .order_by(latest.c.model_id)
                )
            )
            .mappings()
            .all()
        )
        provider = latest.c.record["usage"]
        provider_rows = (
            (
                await session.execute(
                    select(
                        provider["provider"].astext.label("provider"),
                        provider["product"].astext.label("product"),
                        func.count().label("receipts"),
                        func.sum(latest.c.cost).label("cost"),
                        func.count().filter(latest.c.cost.is_(None)).label("unknown_cost_records"),
                    )
                    .where(*predicates, latest.c.record["kind"].astext == "provider")
                    .group_by("provider", "product")
                    .order_by("provider", "product")
                )
            )
            .mappings()
            .all()
        )
        active = await session.scalar(
            select(func.count())
            .select_from(RunRow)
            .where(
                RunRow.id.in_(runs),
                RunRow.status.in_(("accepted", "running")),
            )
        )
    models = [ModelUsage.model_validate(row) for row in model_rows]
    providers = [ProviderCost.model_validate(row) for row in provider_rows]
    known = [row.cost for row in (*models, *providers) if row.cost is not None]
    return UsageSummary.model_validate(
        dict(
            models=models,
            providers=providers,
            active_runs=active or 0,
            requests=sum(row.requests for row in models),
            provider_receipts=sum(row.receipts for row in providers),
            **{name: sum(getattr(row, name) for row in models) for name in TOKEN_COUNTERS},
            audio_seconds=sum_decimal(row.audio_seconds for row in models),
            cost=sum_decimal(known) if known else None,
            unknown_cost_records=sum(row.unknown_cost_records for row in (*models, *providers)),
            incomplete_requests=sum(row.incomplete_requests for row in models),
        )
    )

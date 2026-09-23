"""Asset identity is the scoped upload reference; retirement retains its content."""

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.resources.assets import uploads
from a13n_service.resources.assets.schemas import AssetCreate, AssetPage, AssetView
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.tenancy.authorize import Principal


async def resolve(session: AsyncSession, workspace_id: str, asset_id: str, *, lock: bool = False) -> AssetRow:
    query = select(AssetRow).where(AssetRow.workspace_id == workspace_id, AssetRow.id == asset_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise ServiceError("not_found", "Asset was not found")
    return row


def replay(row: AssetRow, body: AssetCreate) -> AssetView:
    if row.name != body.name:
        raise ServiceError("conflict", "Upload already has an Asset with a different name")
    return AssetView.model_validate(row)


async def create(
    storage: Storage, objects: ObjectStore, actor: Principal, workspace_id: str, body: AssetCreate, *, timeout: float
) -> tuple[AssetView, bool]:
    scope = await uploads.scope_for(storage, actor, workspace_id, "write")
    receipt, _ = await uploads.load(objects, scope, body.upload_id, timeout=timeout)
    reference = uploads.raw_key(scope.organization_id, body.upload_id)
    query = select(AssetRow).where(AssetRow.workspace_id == scope.workspace_id, AssetRow.content_ref == reference)
    try:
        async with transaction(storage) as session:
            await uploads.authorize_scope(session, actor, scope.workspace_id, "write")
            existing = await session.scalar(query)
            if existing is not None:
                return replay(existing, body), False
            row = AssetRow(
                id=new_object_id("ast"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                name=body.name,
                content_type=receipt.content_type,
                size=receipt.size,
                digest=receipt.digest,
                content_ref=reference,
                source=None,
                retired_at=None,
                created_by_id=actor.id,
            )
            session.add(row)
            record(
                session,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                actor_id=actor.id,
                action="asset.create",
                target_kind="asset",
                target_id=row.id,
            )
            await session.flush()
            result = AssetView.model_validate(row)
        return result, True
    except (DBAPIError, TimeoutError, OSError) as error:
        if isinstance(error, IntegrityError) and getattr(error.orig, "sqlstate", None) != "23505":
            raise
        # Resolve concurrent insertion and uncertain commit using the immutable
        # upload identity, never the tentative row ID. Authorization is fresh.
        async with short_session(storage) as session:
            await uploads.authorize_scope(session, actor, scope.workspace_id, "write")
            existing = await session.scalar(query)
            if existing is not None:
                return replay(existing, body), False
        raise ServiceError("unavailable", "Asset creation could not be confirmed; retry the same upload") from error


async def get(storage: Storage, actor: Principal, workspace_id: str, asset_id: str) -> AssetView:
    async with short_session(storage) as session:
        scope = await uploads.authorize_scope(session, actor, workspace_id, "read")
        return AssetView.model_validate(await resolve(session, scope.workspace_id, asset_id))


async def list_assets(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> AssetPage:
    async with short_session(storage) as session:
        scope = await uploads.authorize_scope(session, actor, workspace_id, "read")
        after = cursors.id_position(cursor, "assets", scope.workspace_id)
        rows = (
            await session.scalars(
                select(AssetRow)
                .where(AssetRow.workspace_id == scope.workspace_id, AssetRow.id > after)
                .order_by(AssetRow.id)
                .limit(limit + 1)
            )
        ).all()
        return AssetPage(
            items=[AssetView.model_validate(row) for row in rows[:limit]],
            next_cursor=cursors.encode("assets", scope.workspace_id, rows[limit - 1].id) if len(rows) > limit else None,
        )


async def retire(
    storage: Storage, actor: Principal, workspace_id: str, asset_id: str, *, if_match: str | None
) -> AssetView:
    async with transaction(storage) as session:
        scope = await uploads.authorize_scope(session, actor, workspace_id, "write")
        row = await resolve(session, scope.workspace_id, asset_id, lock=True)
        require_match(if_match, row.id, row.version)
        if row.retired_at is None:
            row.retired_at = await session.scalar(select(func.clock_timestamp()))
            record(
                session,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                actor_id=actor.id,
                action="asset.retire",
                target_kind="asset",
                target_id=row.id,
            )
            await session.flush()
            await session.refresh(row)
        return AssetView.model_validate(row)


async def content(
    storage: Storage, objects: ObjectStore, actor: Principal, workspace_id: str, asset_id: str, *, timeout: float
) -> tuple[AssetView, bytes]:
    async with short_session(storage) as session:
        scope = await uploads.authorize_scope(session, actor, workspace_id, "read")
        row = await resolve(session, scope.workspace_id, asset_id)
        view = AssetView.model_validate(row)
        upload_id = row.content_ref.removeprefix(f"orgs/{scope.organization_id}/uploads/")
    receipt, data = await uploads.load(objects, scope, upload_id, timeout=timeout)
    if (receipt.digest, receipt.size, receipt.content_type) != (view.digest, view.size, view.content_type):
        raise ServiceError("unavailable", "Asset content does not match its immutable metadata")
    await uploads.scope_for(storage, actor, scope.workspace_id, "read")
    return view, data


async def require_usable(session: AsyncSession, workspace_id: str, asset_ids: set[str]) -> None:
    """Input may name only this workspace's unretired assets; existing history keeps retired content readable."""
    if not asset_ids:
        return
    usable = set(
        (
            await session.scalars(
                select(AssetRow.id).where(
                    AssetRow.workspace_id == workspace_id, AssetRow.id.in_(asset_ids), AssetRow.retired_at.is_(None)
                )
            )
        ).all()
    )
    for asset_id in sorted(asset_ids - usable):
        raise ServiceError(
            "invalid_argument", "Asset is not usable in this workspace", {"field": "asset_id", "reason": asset_id}
        )

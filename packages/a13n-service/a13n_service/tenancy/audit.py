"""Workspace-only audit reads; account-wide events never enter a tenant view."""

import asyncio
from datetime import datetime

from a13n_logging import get_logger
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import literal, select, tuple_

from a13n_service.infra.audit import AuditEventRow, record
from a13n_service.infra.cursors import decode, encode
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.authorize import Principal, Scope, authorize
from a13n_service.tenancy.grants import resolve_workspace

logger = get_logger(__name__)


class AuditEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    workspace_id: str
    actor_id: str | None
    action: str
    target_kind: str
    target_id: str
    outcome: str
    details: dict[str, JsonValue]
    occurred_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEvent]
    next_cursor: str | None


async def list_events(
    storage: Storage, actor: Principal, *, workspace_id: str, limit: int, cursor: str | None
) -> AuditPage:
    if not 1 <= limit <= 200:
        raise ServiceError("invalid_argument", "Audit page limit must be between 1 and 200")
    scope = None
    try:
        async with short_session(storage) as session:
            workspace = await resolve_workspace(session, workspace_id)
            workspace_id = workspace.id
            scope = Scope(workspace.organization_id, workspace.id)
            authorize(actor, scope, "admin")
            query = select(AuditEventRow).where(
                AuditEventRow.organization_id == scope.organization_id, AuditEventRow.workspace_id == workspace_id
            )
            if cursor is not None:
                try:
                    occurred_at, event_id = decode(cursor, "audit", workspace_id)
                    if not isinstance(occurred_at, str) or not isinstance(event_id, str):
                        raise ValueError("position")
                    timestamp = datetime.fromisoformat(occurred_at)
                    if timestamp.tzinfo is None:
                        raise ValueError("timestamp")
                except (ValueError, TypeError):
                    raise ServiceError("invalid_cursor", "Invalid audit cursor") from None
                query = query.where(
                    tuple_(AuditEventRow.occurred_at, AuditEventRow.id) < tuple_(literal(timestamp), literal(event_id))
                )
            rows = (
                await session.scalars(
                    query.order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc()).limit(limit + 1)
                )
            ).all()
            items = [AuditEvent.model_validate(row) for row in rows[:limit]]
    except ServiceError as error:
        if error.code == "forbidden" and scope is not None:
            # The rejected read session has closed before the independent evidence transaction.
            try:
                async with asyncio.timeout(2), transaction(storage) as session:
                    record(
                        session,
                        organization_id=scope.organization_id,
                        workspace_id=scope.workspace_id,
                        actor_id=actor.id,
                        action="audit.read",
                        target_kind="workspace",
                        target_id=workspace_id,
                        outcome="denied",
                        details={
                            "verb": "admin",
                            "credential_workspace_id": actor.confinement.workspace_id if actor.confinement else None,
                        },
                    )
            except Exception as failure:
                logger.error("Admin denial audit failed", extra={"error_type": type(failure).__name__})
        raise
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode("audit", workspace_id, last.occurred_at.isoformat(), last.id)
    return AuditPage(items=items, next_cursor=next_cursor)

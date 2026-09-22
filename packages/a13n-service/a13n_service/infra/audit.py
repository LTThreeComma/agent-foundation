"""Append-only, bounded audit evidence, written with the owning mutation."""

import json
from datetime import datetime
from typing import Literal

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base
from a13n_service.infra.ids import new_object_id

_GLOBAL_ACTIONS = frozenset({("session.create", "session"), ("session.revoke", "session")})


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'denied', 'failed')", name="outcome"),
        CheckConstraint("organization_id IS NOT NULL OR workspace_id IS NULL", name="global_scope"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    action: Mapped[str]
    target_kind: Mapped[str]
    target_id: Mapped[str]
    outcome: Mapped[str]
    details: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def record(
    session: AsyncSession,
    *,
    organization_id: str | None,
    workspace_id: str | None,
    actor_id: str | None,
    action: str,
    target_kind: str,
    target_id: str,
    outcome: Literal["ok", "denied", "failed"] = "ok",
    details: dict[str, JsonValue] | None = None,
) -> None:
    """Append to the caller's mutation transaction with explicit actual scope."""
    if organization_id is None and (workspace_id is not None or (action, target_kind) not in _GLOBAL_ACTIONS):
        raise ValueError("Only declared account-wide audit actions may omit organization scope")
    if organization_id is not None and (action, target_kind) in _GLOBAL_ACTIONS:
        raise ValueError("Account-wide audit actions cannot be assigned to a tenant")
    payload = dict(details or {})
    if len(json.dumps(payload, allow_nan=False).encode()) > 8192:
        raise ValueError("Audit details exceed their byte limit")
    session.add(
        AuditEventRow(
            id=new_object_id("audit"),
            organization_id=organization_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            outcome=outcome,
            details=payload,
        )
    )

"""Durable at-least-once delivery rows: enqueue with the owning state change, claim, settle.

The outbox owns persistence, claiming and settlement only. Owners register one handler per `kind`;
a handler runs outside any database session for external I/O and settles its claim itself, so an
internal delivery can settle in the same transaction that applies it.
"""

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from a13n_logging import get_logger
from pydantic import JsonValue
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    delete,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import Base, Storage, now, transaction
from a13n_service.infra.ids import new_object_id

logger = get_logger(__name__)

type OutboxKind = Literal["webhook", "child_result", "email"]


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("kind", "dedupe_key"),
        CheckConstraint("kind IN ('webhook', 'child_result', 'email')", name="kind"),
        CheckConstraint("status IN ('pending', 'delivered', 'dead')", name="status"),
        CheckConstraint("(status = 'delivered') = (delivered_at IS NOT NULL)", name="delivered"),
        Index("ix_outbox_due", "kind", "available_at", postgresql_where=text("status = 'pending'")),
        Index("ix_outbox_settled", "created_at", postgresql_where=text("status <> 'pending'")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    kind: Mapped[str]
    dedupe_key: Mapped[str]
    # Everything delivery needs is copied here; `subscription_id` is diagnostic provenance, not a foreign key.
    target: Mapped[dict] = mapped_column(JSONB)
    payload: Mapped[dict] = mapped_column(JSONB)
    subscription_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(server_default="0")
    lease_owner: Mapped[str | None]
    lease_token_hash: Mapped[str | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    kind: str
    organization_id: str
    workspace_id: str | None
    target: dict
    payload: dict
    attempts: int
    token: str


type Handler = Callable[[Claim], Awaitable[None]]


def enqueue(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    kind: OutboxKind,
    target: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
    dedupe_key: str | None = None,
    subscription_id: str | None = None,
    row_id: str | None = None,
) -> str:
    """Stage a delivery in the caller's transaction; without a dedupe key the row ID is its own identity."""
    identity = row_id or new_object_id("obx")
    session.add(
        OutboxRow(
            id=identity,
            organization_id=organization_id,
            workspace_id=workspace_id,
            kind=kind,
            dedupe_key=dedupe_key or identity,
            target=dict(target),
            payload=dict(payload),
            subscription_id=subscription_id,
        )
    )
    return identity


async def enqueue_once(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str | None,
    kind: OutboxKind,
    dedupe_key: str,
    target: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> None:
    """Idempotent staging for deliveries keyed by a durable fact, such as one sealed child run."""
    await session.execute(
        insert(OutboxRow)
        .values(
            id=new_object_id("obx"),
            organization_id=organization_id,
            workspace_id=workspace_id,
            kind=kind,
            dedupe_key=dedupe_key,
            target=dict(target),
            payload=dict(payload),
        )
        .on_conflict_do_nothing(index_elements=["kind", "dedupe_key"])
    )


async def claim(storage: Storage, kind: OutboxKind, *, owner: str, limit: int, lease_seconds: float) -> list[Claim]:
    async with transaction(storage) as session:
        current = await now(session)
        rows = (
            await session.scalars(
                select(OutboxRow)
                .where(
                    OutboxRow.kind == kind,
                    OutboxRow.status == "pending",
                    OutboxRow.available_at <= current,
                    or_(OutboxRow.lease_expires_at.is_(None), OutboxRow.lease_expires_at <= current),
                )
                .order_by(OutboxRow.available_at, OutboxRow.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        claims = []
        for row in rows:
            token = secrets.token_urlsafe(32)
            row.attempts += 1
            row.lease_owner = owner
            row.lease_token_hash = secret_hash(token)
            row.lease_expires_at = current + timedelta(seconds=lease_seconds)
            claims.append(
                Claim(
                    id=row.id,
                    kind=row.kind,
                    organization_id=row.organization_id,
                    workspace_id=row.workspace_id,
                    target=row.target,
                    payload=row.payload,
                    attempts=row.attempts,
                    token=token,
                )
            )
        return claims


async def settle(
    session: AsyncSession,
    claimed: Claim,
    outcome: Literal["delivered", "retry", "dead"],
    *,
    error: str | None = None,
    retry_after: float = 0,
) -> bool:
    """Apply an outcome only while this claim still holds the lease; a stale sender changes nothing."""
    row = await session.get(OutboxRow, claimed.id, with_for_update=True)
    if row is None or row.status != "pending" or row.lease_token_hash != secret_hash(claimed.token):
        return False
    current = await now(session)
    row.lease_owner = row.lease_token_hash = row.lease_expires_at = None
    row.last_error = error[:1024] if error else None
    if outcome == "delivered":
        row.status, row.delivered_at = "delivered", current
    elif outcome == "dead":
        row.status = "dead"
    else:
        row.available_at = current + timedelta(seconds=retry_after)
    return True


def backoff(attempts: int, *, base: float = 2, cap: float = 3600) -> float:
    return min(cap, base ** min(attempts, 16))


async def deliver(
    storage: Storage, handlers: Mapping[str, Handler], *, owner: str, limit: int, lease_seconds: float
) -> None:
    """One bounded pass per kind. A handler that raises leaves its row for a later retry with backoff."""
    for kind, handler in handlers.items():
        for claimed in await claim(storage, kind, owner=owner, limit=limit, lease_seconds=lease_seconds):  # type: ignore[arg-type]
            try:
                await handler(claimed)
            except Exception as error:
                logger.warning(
                    "Outbox delivery failed", extra={"outbox_id": claimed.id, "error_type": type(error).__name__}
                )
                async with transaction(storage) as session:
                    await settle(
                        session, claimed, "retry", error=type(error).__name__, retry_after=backoff(claimed.attempts)
                    )


async def purge_settled(storage: Storage, *, older_than: timedelta, limit: int) -> int:
    """Bounded retention for delivered and dead rows; pending rows are never removed."""
    async with transaction(storage) as session:
        cutoff = await now(session) - older_than
        ids = (
            await session.scalars(
                select(OutboxRow.id)
                .where(OutboxRow.status != "pending", OutboxRow.created_at < cutoff)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if ids:
            await session.execute(delete(OutboxRow).where(OutboxRow.id.in_(ids)))
        return len(ids)

"""SQL-only authorization invalidation and encrypted flow/token storage."""

import hashlib
import json
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.crypto import Envelope, KeyRing, SecretLocation
from a13n_service.resources.connections.tables import ConnectionAuthorizationRow, ConnectionRow


def identity(connection: ConnectionRow) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "url": connection.config["url"],
                "oauth": connection.config.get("oauth"),
                "auth": connection.auth,
                "credential": connection.credential,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def seal(row: ConnectionAuthorizationRow, value: dict, keys: KeyRing) -> None:
    row.credential = keys.protect(
        json.dumps(value).encode(), SecretLocation(row.organization_id, row.__tablename__, "credential", row.id)
    ).model_dump(mode="json")


def reveal(row: ConnectionAuthorizationRow, keys: KeyRing) -> dict:
    assert row.credential is not None
    return json.loads(
        keys.reveal(
            Envelope.model_validate(row.credential),
            SecretLocation(row.organization_id, row.__tablename__, "credential", row.id),
        )
    )


def invalidate(
    row: ConnectionAuthorizationRow,
    status: Literal["revoked", "reauthorization_required"],
    reason: str,
    *,
    retain_operation: bool = False,
) -> None:
    row.status = status
    row.credential = None
    row.oauth_state_hash = None
    row.expires_at = None
    row.failure = {"reason": reason}
    if not retain_operation:
        row.generation += 1
        row.operation_id = row.operation_kind = row.operation_deadline = None


async def invalidate_connection(session: AsyncSession, connection_id: str) -> None:
    rows = await session.scalars(
        select(ConnectionAuthorizationRow)
        .where(ConnectionAuthorizationRow.connection_id == connection_id)
        .order_by(ConnectionAuthorizationRow.id)
        .with_for_update()
    )
    for row in rows:
        invalidate(row, "reauthorization_required", "connection_changed")

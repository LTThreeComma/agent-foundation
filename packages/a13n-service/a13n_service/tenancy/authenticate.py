"""Password verification outside SQL and bounded, detached credential authentication."""

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from anyio.to_thread import run_sync
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from sqlalchemy import func, or_, select

from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy.authorize import Principal, Scope
from a13n_service.tenancy.credentials import ApiKeyRow, TokenRow
from a13n_service.tenancy.grants import principal_for, workspace_scope
from a13n_service.tenancy.tables import PasswordRow, PrincipalRow

COOKIE_NAME = "__Host-a13n_session"


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def session_csrf(secret: str) -> str:
    """Stable across tabs, without persisting a recoverable session secret."""
    return hmac.new(secret.encode(), b"a13n:session:csrf:v1", hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class Login:
    principal: Principal
    secret: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class Authenticated:
    principal: Principal
    credential_id: str
    kind: Literal["session", "key"]


async def login(storage: Storage, *, email: str, password: str, session_seconds: int) -> Login:
    async with short_session(storage) as session:
        stored = (
            await session.execute(
                select(PrincipalRow.id, PasswordRow.hash)
                .join(PasswordRow, PrincipalRow.id == PasswordRow.principal_id)
                .where(PrincipalRow.email == email, PrincipalRow.status == "active")
            )
        ).one_or_none()
    # Missing identities still perform the expensive password operation.
    if stored is None:
        await run_sync(PasswordHasher().hash, password)
        raise ServiceError("unauthenticated", "Invalid email or password")
    principal_id, password_hash = stored
    try:
        await run_sync(PasswordHasher().verify, password_hash, password)
    except VerificationError:
        raise ServiceError("unauthenticated", "Invalid email or password") from None
    secret = secrets.token_urlsafe(32)
    csrf = session_csrf(secret)
    async with transaction(storage) as session:
        current = await session.get(PasswordRow, principal_id, with_for_update=True)
        if current is None or current.hash != password_hash:
            raise ServiceError("unauthenticated", "Invalid email or password")
        principal = await principal_for(session, principal_id)
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        token_id = new_object_id("token")
        session.add(
            TokenRow(
                id=token_id,
                principal_id=principal_id,
                kind="session",
                secret_hash=secret_hash(secret),
                data={"csrf_hash": secret_hash(csrf)},
                expires_at=now + timedelta(seconds=session_seconds),
            )
        )
        record(
            session,
            organization_id=None,
            workspace_id=None,
            actor_id=principal_id,
            action="session.create",
            target_kind="session",
            target_id=token_id,
        )
    return Login(principal, secret, csrf)


async def authenticate(
    storage: Storage,
    *,
    secret: str,
    kind: Literal["session", "key"],
    csrf_token: str | None,
    mutation: bool,
    session_seconds: int,
) -> Authenticated:
    if not secret or len(secret) > 512:
        raise ServiceError("unauthenticated", "Authentication is required")
    async with transaction(storage) as session:
        if kind == "session":
            token = await session.scalar(
                select(TokenRow)
                .where(
                    TokenRow.secret_hash == secret_hash(secret),
                    TokenRow.kind == "session",
                    TokenRow.revoked_at.is_(None),
                    TokenRow.expires_at > func.clock_timestamp(),
                )
                .with_for_update()
            )
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if token is None or token.expires_at <= now:
                raise ServiceError("unauthenticated", "Authentication is required")
            expected = (token.data or {}).get("csrf_hash")
            if mutation and (
                not isinstance(expected, str)
                or not csrf_token
                or not hmac.compare_digest(expected, secret_hash(csrf_token))
            ):
                raise ServiceError("forbidden", "CSRF validation failed")
            principal = await principal_for(session, token.principal_id)
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            token.expires_at = now + timedelta(seconds=session_seconds)
            return Authenticated(principal, token.id, "session")
        key = await session.scalar(
            select(ApiKeyRow)
            .where(
                ApiKeyRow.secret_hash == secret_hash(secret),
                ApiKeyRow.revoked_at.is_(None),
                or_(ApiKeyRow.expires_at.is_(None), ApiKeyRow.expires_at > func.clock_timestamp()),
            )
            .with_for_update()
        )
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if key is None or (key.expires_at is not None and key.expires_at <= now):
            raise ServiceError("unauthenticated", "Authentication is required")
        principal = await principal_for(
            session, key.principal_id, confinement=Scope(key.organization_id, key.workspace_id)
        )
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        if key.last_used_at is None or key.last_used_at <= now - timedelta(minutes=1):
            key.last_used_at = now
        return Authenticated(principal, key.id, "key")


async def issue_user_key(storage: Storage, actor: Principal, *, workspace_id: str, name: str) -> tuple[str, str]:
    if actor.kind != "user":
        raise ServiceError("forbidden", "User keys require a user identity")
    secret = "a13n_" + secrets.token_urlsafe(32)
    key_id = new_object_id("key")
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        session.add(
            ApiKeyRow(
                id=key_id,
                organization_id=scope.organization_id,
                workspace_id=workspace_id,
                principal_id=actor.id,
                name=name,
                secret_hash=secret_hash(secret),
                created_by_id=actor.id,
            )
        )
        record(
            session,
            organization_id=scope.organization_id,
            workspace_id=workspace_id,
            actor_id=actor.id,
            action="credential.create",
            target_kind="api_key",
            target_id=key_id,
        )
    return key_id, secret


async def logout(storage: Storage, credential: Authenticated) -> None:
    if credential.kind != "session":
        raise ServiceError("invalid_argument", "Logout requires a login session")
    async with transaction(storage) as session:
        token = await session.get(TokenRow, credential.credential_id, with_for_update=True)
        if token is not None and token.revoked_at is None:
            token.revoked_at = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            record(
                session,
                organization_id=None,
                workspace_id=None,
                actor_id=credential.principal.id,
                action="session.revoke",
                target_kind="session",
                target_id=token.id,
            )

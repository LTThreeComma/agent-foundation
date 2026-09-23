"""Immutable upload intent reservation, followed by exact raw publication."""

import asyncio
import hashlib
import json

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.objects.local import LocalObjects, ObjectConflict, ObjectCorrupt, StoredObject
from a13n_service.resources.assets.schemas import UploadIntent, UploadReceipt, UploadView
from a13n_service.tenancy.authorize import Principal, Scope, Verb
from a13n_service.tenancy.grants import principal_for, workspace_scope


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def raw_key(organization_id: str, upload_id: str) -> str:
    return f"orgs/{organization_id}/uploads/{upload_id}"


async def authorize_scope(session: AsyncSession, actor: Principal, workspace_id: str, verb: Verb) -> Scope:
    current = await principal_for(session, actor.id, confinement=actor.confinement)
    return await workspace_scope(session, current, workspace_id, verb)


async def scope_for(storage: Storage, actor: Principal, workspace_id: str, verb: Verb) -> Scope:
    async with short_session(storage) as session:
        return await authorize_scope(session, actor, workspace_id, verb)


def request_key(value: str | None) -> str:
    if (
        value is None
        or not 1 <= len(value) <= 128
        or not value.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ServiceError("invalid_argument", "A bounded printable Idempotency-Key is required")
    return value


async def publish(objects: LocalObjects, key: str, content: bytes, *, timeout: float, intent: bool) -> StoredObject:
    try:
        async with asyncio.timeout(timeout):
            result = await objects.create_payload(key, content)
    except (TimeoutError, OSError, ObjectConflict):
        # A lost acknowledgement may follow durable publication. Read back the
        # same immutable key; never write new bytes or infer success from existence.
        try:
            async with asyncio.timeout(timeout):
                result = await objects.read(key)
        except (TimeoutError, OSError, ObjectCorrupt):
            raise ServiceError("unavailable", "Upload publication could not be confirmed") from None
        if result is None:
            raise ServiceError("unavailable", "Upload publication could not be confirmed") from None
    except ObjectCorrupt:
        raise ServiceError("unavailable", "Upload storage is corrupt") from None
    if result.content != content or result.writer is not None:
        raise ServiceError(
            "conflict" if intent else "unavailable",
            "Upload key was used for different content or metadata"
            if intent
            else "Upload bytes do not match their receipt",
        )
    return result


async def stage(
    storage: Storage,
    objects: LocalObjects,
    actor: Principal,
    workspace_id: str,
    *,
    key: str,
    filename: str,
    content_type: str,
    content: bytes,
    max_bytes: int,
    timeout: float,
) -> UploadView:
    scope = await scope_for(storage, actor, workspace_id, "write")
    assert scope.workspace_id is not None
    if len(content) > min(max_bytes, objects.max_bytes):
        raise ServiceError("payload_too_large", "Upload exceeds its byte limit")
    identity = (
        "upl_"
        + hashlib.sha256(
            canonical(["a13n.service.upload.v1", scope.organization_id, scope.workspace_id, actor.id, request_key(key)])
        ).hexdigest()
    )
    intent = {
        "id": identity,
        "organization_id": scope.organization_id,
        "workspace_id": scope.workspace_id,
        "principal_id": actor.id,
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "digest": hashlib.sha256(content).hexdigest(),
    }
    try:
        normalized = UploadIntent.model_validate(intent).model_dump(mode="json")
        receipt = UploadReceipt.model_validate(
            {**normalized, "request_digest": hashlib.sha256(canonical(normalized)).hexdigest()}
        )
    except ValidationError:
        raise ServiceError("invalid_argument", "Upload filename or content type is invalid") from None
    # Reserve normalized complete intent before publishing its bytes. A crash
    # between these writes is repaired only by the exact same explicit request.
    object_key = raw_key(scope.organization_id, identity)
    await publish(
        objects, object_key + ".receipt.json", canonical(receipt.model_dump(mode="json")), timeout=timeout, intent=True
    )
    await publish(objects, object_key, content, timeout=timeout, intent=False)
    await scope_for(storage, actor, scope.workspace_id, "write")
    return UploadView(
        upload_id=identity,
        filename=receipt.filename,
        content_type=receipt.content_type,
        size=receipt.size,
        digest=receipt.digest,
    )


async def load(objects: LocalObjects, scope: Scope, upload_id: str, *, timeout: float) -> tuple[UploadReceipt, bytes]:
    key = raw_key(scope.organization_id, upload_id)
    try:
        async with asyncio.timeout(timeout):
            stored = await objects.read(key + ".receipt.json")
            if stored is None:
                raise ServiceError("not_found", "Upload was not found")
            receipt = UploadReceipt.model_validate_json(stored.content)
            intent = receipt.model_dump(mode="json", exclude={"request_digest"})
            if stored.writer is not None or hashlib.sha256(canonical(intent)).hexdigest() != receipt.request_digest:
                raise ServiceError("unavailable", "Upload receipt is corrupt")
            if (
                receipt.id != upload_id
                or receipt.organization_id != scope.organization_id
                or receipt.workspace_id != scope.workspace_id
            ):
                raise ServiceError("not_found", "Upload was not found")
            raw = await objects.read(key)
    except (TimeoutError, OSError, ObjectCorrupt, ValidationError):
        raise ServiceError("unavailable", "Upload content is unavailable") from None
    if raw is None or raw.writer is not None or raw.digest != receipt.digest or len(raw.content) != receipt.size:
        raise ServiceError("unavailable", "Upload content is incomplete or corrupt")
    return receipt, raw.content

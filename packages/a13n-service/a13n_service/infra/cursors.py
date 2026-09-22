"""Bounded, opaque pagination positions bound to one resource collection."""

import base64
import json

from pydantic import JsonValue

from a13n_service.infra.errors import ServiceError


def encode(kind: str, owner: str, *position: JsonValue) -> str:
    return base64.urlsafe_b64encode(json.dumps([kind, owner, *position], separators=(",", ":")).encode()).decode()


def decode(cursor: str, kind: str, owner: str) -> list[JsonValue]:
    try:
        if len(cursor) > 2048:
            raise ValueError("oversized")
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) < 3 or value[:2] != [kind, owner]:
            raise ValueError("scope")
        return value[2:]
    except (ValueError, TypeError):
        raise ServiceError("invalid_cursor", "Invalid collection cursor") from None

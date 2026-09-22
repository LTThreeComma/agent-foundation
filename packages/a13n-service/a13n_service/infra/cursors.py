"""Bounded, opaque pagination positions bound to one resource collection."""

import base64
import json
from datetime import datetime

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


def id_position(cursor: str | None, kind: str, owner: str) -> str:
    if cursor is None:
        return ""
    position = decode(cursor, kind, owner)
    if len(position) != 1 or not isinstance(position[0], str) or len(position[0]) > 72:
        raise ServiceError("invalid_cursor", "Invalid collection cursor")
    return position[0]


def time_position(cursor: str, kind: str, owner: str) -> tuple[datetime, str]:
    values = decode(cursor, kind, owner)
    try:
        if len(values) != 2 or not isinstance(values[0], str) or not isinstance(values[1], str):
            raise ValueError("position")
        timestamp = datetime.fromisoformat(values[0])
        if timestamp.tzinfo is None or len(values[1]) > 72:
            raise ValueError("position")
        return timestamp, values[1]
    except ValueError:
        raise ServiceError("invalid_cursor", "Invalid collection cursor") from None

"""One detached error contract shared by Service operations and HTTP."""

from typing import Literal

from pydantic import JsonValue

type ErrorCode = Literal[
    "invalid_argument",
    "invalid_cursor",
    "unauthenticated",
    "forbidden",
    "not_found",
    "already_exists",
    "conflict",
    "precondition_failed",
    "precondition_required",
    "payload_too_large",
    "disabled",
    "unavailable",
    "rate_limited",
    "internal",
]


class ServiceError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, JsonValue] | None = None):
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.details = dict(details or {})


def not_found(kind: str, resource_id: str) -> ServiceError:
    return ServiceError("not_found", f"{kind} {resource_id} not found", {"kind": kind, "id": resource_id})


def conflict(kind: str, resource_id: str, reason: str) -> ServiceError:
    """A state rule refused the operation; `reason` is a stable machine-readable word."""
    message = f"{kind} {resource_id}: {reason.replace('_', ' ')}"
    return ServiceError("conflict", message, {"kind": kind, "id": resource_id, "reason": reason})


def disabled(kind: str, resource_id: str) -> ServiceError:
    return ServiceError("disabled", f"{kind} {resource_id} is disabled", {"kind": kind, "id": resource_id})


def invalid(field: str, reason: str) -> ServiceError:
    return ServiceError("invalid_argument", f"{field}: {reason}", {"field": field, "reason": reason})

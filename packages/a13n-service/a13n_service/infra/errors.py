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

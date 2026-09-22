"""Current HTTP error envelope and strong resource preconditions."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from a13n_service.infra.errors import ErrorCode, ServiceError

_STATUS: dict[ErrorCode, int] = {
    "invalid_argument": 400,
    "invalid_cursor": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "already_exists": 409,
    "conflict": 409,
    "precondition_failed": 412,
    "precondition_required": 428,
    "payload_too_large": 413,
    "disabled": 422,
    "unavailable": 503,
    "rate_limited": 429,
    "internal": 500,
}


async def service_error_response(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ServiceError):
        raise error
    headers = {}
    if error.code == "rate_limited":
        headers["Retry-After"] = str(error.details.get("retry_after", 1))
    return JSONResponse(
        {"error": {"code": error.code, "message": error.message, "details": error.details}},
        status_code=_STATUS[error.code],
        headers=headers,
    )


async def validation_error_response(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    # Pydantic input and custom messages can contain secrets; expose only locations and codes.
    fields = [
        {"field": ".".join(str(part) for part in item["loc"]), "reason": item["type"]} for item in error.errors()[:20]
    ]
    return JSONResponse(
        {"error": {"code": "invalid_argument", "message": "Request validation failed", "details": {"fields": fields}}},
        status_code=400,
    )


def etag(resource_id: str, version: int) -> str:
    return f'"{resource_id}:{version}"'


def require_match(value: str | None, resource_id: str, version: int) -> None:
    current = etag(resource_id, version)
    if value is None:
        raise ServiceError("precondition_required", "If-Match is required", {"header": "If-Match"})
    if value != current:
        raise ServiceError("precondition_failed", "Resource changed", {"current_etag": current})

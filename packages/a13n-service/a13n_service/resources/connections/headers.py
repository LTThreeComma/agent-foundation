"""One normalized header contract for Connection auth and caller context; `check_header_name` owns the names."""

from collections.abc import Mapping

from a13n_service.providers.tools.mcp import check_header_name


def normalize_headers(headers: Mapping[str, str], *, authentication: bool = False) -> dict[str, str]:
    if len(headers) > 32:
        raise ValueError("At most 32 headers are allowed")
    normalized: dict[str, str] = {}
    size = 0
    for name, value in headers.items():
        folded = check_header_name(name.lower(), authentication=authentication)
        if folded in normalized:
            raise ValueError("Header names must be unique ignoring case")
        if len(value) > 4096 or any(ord(char) < 32 or ord(char) >= 127 for char in value):
            raise ValueError("Header values must contain only printable ASCII characters")
        size += len(name) + len(value)
        if size > 8192:
            raise ValueError("Headers exceed their byte limit")
        normalized[folded] = value
    return normalized

"""One normalized header contract for Connection auth and caller context."""

import re
from collections.abc import Mapping

_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9a-z-]+$")
_FORBIDDEN = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "accept",
        "accept-encoding",
        "cookie",
        "set-cookie",
        "host",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def normalize_headers(headers: Mapping[str, str], *, authentication: bool = False) -> dict[str, str]:
    if len(headers) > 32:
        raise ValueError("At most 32 headers are allowed")
    normalized: dict[str, str] = {}
    size = 0
    for name, value in headers.items():
        folded = name.lower()
        if not _NAME.fullmatch(folded) or len(name) > 128 or folded in normalized:
            raise ValueError("Header names must be valid and unique ignoring case")
        if folded.startswith(("proxy-", "mcp-", "sec-")) or (
            folded in _FORBIDDEN and not (authentication and folded == "authorization")
        ):
            raise ValueError("Reserved HTTP header")
        if len(value) > 4096 or any(ord(char) < 32 or ord(char) >= 127 for char in value):
            raise ValueError("Header values must contain only printable ASCII characters")
        size += len(name) + len(value)
        if size > 8192:
            raise ValueError("Headers exceed their byte limit")
        normalized[folded] = value
    return normalized

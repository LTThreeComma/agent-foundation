"""Shared Service object-ID allocation."""

import re
import secrets
from typing import Annotated

from pydantic import StringConstraints

# Existing lowercase alphanumeric IDs remain valid; allocation is narrower than acceptance.
ObjectId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[a-z0-9]{16,64}$", max_length=72)]

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]{1,7}$")
# Tiers and their lifetime volume budgets are owned by spec/data-conventions.md#service-id-allocation.
# Every kind not listed here allocates 32 hex characters (128 random bits).
_RANDOM_BYTES = {
    **dict.fromkeys(("ap", "cnr", "conn", "envp", "envtpl", "mdl", "mprov", "org", "sa", "sk", "usr", "ws"), 10),
    **dict.fromkeys(("apr", "ast", "env", "inv", "rb", "sess", "skr"), 12),
    **dict.fromkeys(("inb", "rat", "run"), 14),
}


def new_object_id(kind: str) -> str:
    """Allocate one unpredictable Service object ID for a kind prefix."""
    if _KIND_PATTERN.fullmatch(kind) is None:
        raise ValueError("object ID kind must be 2-8 lowercase ASCII letters or digits")
    return f"{kind}_{secrets.token_hex(_RANDOM_BYTES.get(kind, 16))}"

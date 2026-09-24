import base64
from dataclasses import replace

import pytest
from a13n_service.infra.crypto import KeyRing, SecretLocation
from a13n_service.infra.errors import ServiceError
from pydantic import SecretStr


def test_credentials_are_randomized_bound_and_survive_key_rotation():
    old_key = SecretStr(base64.b64encode(bytes(range(32))).decode())
    new_key = SecretStr(base64.b64encode(bytes(reversed(range(32)))).decode())
    old = KeyRing(active_key_id="old", keys={"old": old_key})
    location = SecretLocation("org_one", "model_providers", "credential", "mp_one")
    envelope = old.protect(b"private credential", location)
    assert old.protect(b"private credential", location) != envelope
    rotated = KeyRing(active_key_id="new", keys={"old": old_key, "new": new_key})
    assert rotated.reveal(envelope, location) == b"private credential"
    assert rotated.protect(b"private credential", location).key_id == "new"
    for field in ("organization_id", "table", "column", "row_id"):
        with pytest.raises(ServiceError, match="cannot be decrypted"):
            rotated.reveal(envelope, replace(location, **{field: "different"}))
    with pytest.raises(ServiceError, match="cannot be decrypted"):
        KeyRing(active_key_id="new", keys={"new": new_key}).reveal(envelope, location)
    with pytest.raises(ServiceError, match="not configured"):
        KeyRing(active_key_id=None, keys={}).protect(b"secret", location)

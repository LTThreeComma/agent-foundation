"""Detached encrypted material for managed Secrets, without domain authorization."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedSecret:
    secret_id: str
    organization_id: str
    workspace_id: str
    owner_type: str
    owner_id: str
    key: str
    version: int
    ciphertext: bytes
    nonce: bytes
    encryption_key_id: str

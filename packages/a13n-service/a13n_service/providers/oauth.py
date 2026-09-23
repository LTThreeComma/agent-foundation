"""OAuth wire values and bounded discovery; no persistence or automatic auth handler."""

from typing import Literal
from urllib.parse import urlparse

import httpx2
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from a13n_service.infra.errors import ServiceError


class OAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    issuer: str = Field(min_length=1, max_length=2048)
    client_id: str = Field(min_length=1, max_length=1024)
    scopes: tuple[str, ...] = Field(default=(), max_length=32)
    token_endpoint_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "none"

    @field_validator("issuer")
    @classmethod
    def issuer_url(cls, value: str) -> str:
        parts = urlparse(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.query or parts.fragment:
            raise ValueError("Issuer must be an absolute HTTP URL without credentials, query or fragment")
        return value

    @field_validator("scopes")
    @classmethod
    def scope_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not scope or len(scope) > 128 or any(ord(c) < 33 or ord(c) > 126 or c in '\\"' for c in scope)
            for scope in value
        ):
            raise ValueError("Scopes must be unique bounded OAuth scope tokens")
        return value


class OAuthEndpoints(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    refresh_supported: bool
    response_issuer_required: bool


class TokenResponse(BaseModel):
    access_token: SecretStr = Field(min_length=1, max_length=8192)
    token_type: str
    refresh_token: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    expires_in: int | None = Field(default=None, ge=0, le=31536000)
    scope: str | None = Field(default=None, max_length=8192)

    @field_validator("token_type")
    @classmethod
    def bearer_only(cls, value: str) -> str:
        if value.lower() != "bearer":
            raise ValueError("Only Bearer OAuth tokens are supported")
        return "Bearer"

    @field_validator("access_token")
    @classmethod
    def safe_bearer(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("Invalid bearer token")
        return value


async def discover(client: httpx2.AsyncClient, resource: str, config: OAuthConfig) -> OAuthEndpoints:
    parsed = urlparse(resource)
    if parsed.query or parsed.fragment or parsed.username:
        raise ServiceError("invalid_argument", "OAuth resource URL must not contain credentials, query or fragment")
    response = await client.get(resource)
    metadata = None
    for url in build_protected_resource_metadata_discovery_urls(
        extract_resource_metadata_from_www_auth(response), resource
    ):
        response = await client.get(url)
        if response.status_code in {404, 405}:
            continue
        response.raise_for_status()
        metadata = ProtectedResourceMetadata.model_validate_json(response.content)
        break
    if (
        metadata is None
        or str(metadata.resource) != resource
        or config.issuer not in [str(issuer) for issuer in (metadata.authorization_servers or [])]
    ):
        raise ServiceError("invalid_argument", "OAuth resource does not advertise the configured issuer")
    server = None
    for url in build_oauth_authorization_server_metadata_discovery_urls(config.issuer, resource):
        response = await client.get(url)
        if response.status_code in {404, 405}:
            continue
        response.raise_for_status()
        server = OAuthMetadata.model_validate_json(response.content)
        validate_metadata_issuer(server, config.issuer)
        break
    if server is None or not server.authorization_endpoint or not server.token_endpoint:
        raise ServiceError("invalid_argument", "OAuth issuer metadata is unavailable")
    grants = (
        server.grant_types_supported if server.grant_types_supported is not None else ["authorization_code", "implicit"]
    )
    methods = (
        server.token_endpoint_auth_methods_supported
        if server.token_endpoint_auth_methods_supported is not None
        else ["client_secret_basic"]
    )
    if "code" not in server.response_types_supported or "authorization_code" not in grants:
        raise ServiceError("invalid_argument", "OAuth issuer does not support authorization codes")
    if (
        "S256" not in (server.code_challenge_methods_supported or [])
        or config.token_endpoint_auth_method not in methods
    ):
        raise ServiceError(
            "invalid_argument", "OAuth issuer does not support the configured PKCE/client authentication"
        )
    return OAuthEndpoints(
        resource=resource,
        issuer=config.issuer,
        authorization_endpoint=str(server.authorization_endpoint),
        token_endpoint=str(server.token_endpoint),
        refresh_supported="refresh_token" in grants,
        response_issuer_required=bool(getattr(server, "authorization_response_iss_parameter_supported", False)),
    )

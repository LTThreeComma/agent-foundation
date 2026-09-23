"""A signed-in session on the local Service's public HTTP API, for seeding and private resources."""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any
from uuid import uuid4

import httpx2

type Json = dict[str, Any]

SEALED = frozenset({"waiting", "completed", "failed", "cancelled"})


class ApiError(RuntimeError):
    pass


class Api:
    def __init__(self, base_url: str) -> None:
        self._http = httpx2.Client(base_url=base_url, trust_env=False, timeout=30)

    def __enter__(self) -> Api:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, trace: TracebackType | None
    ) -> None:
        self._http.close()

    def login(self, email: str, password: str) -> None:
        response = self._send("POST", "/api/v1/auth/login", json={"email": email, "password": password})
        # The session cookie is Secure, which a cookie jar withholds over plain HTTP; loopback sends it explicitly.
        self._http.headers["cookie"] = "; ".join(f"{name}={value}" for name, value in response.cookies.items())
        self._http.headers["x-csrf-token"] = response.json()["csrf_token"]

    def get(self, path: str) -> Json:
        return self._send("GET", path).json()

    def items(self, path: str) -> list[Json]:
        """Every item of a collection, across pages."""
        found: list[Json] = []
        cursor: str | None = None
        while True:
            page = self._send("GET", path, params={"limit": 100, **({"cursor": cursor} if cursor else {})}).json()
            found += page["items"]
            if not (cursor := page["next_cursor"]):
                return found

    def post(
        self,
        path: str,
        body: Json | None = None,
        *,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        idempotent: bool = False,
    ) -> Json:
        """`idempotent` sends the Idempotency-Key that commands creating work require."""
        headers = {"idempotency-key": uuid4().hex} if idempotent else {}
        return self._send("POST", path, json=body, files=files, headers=headers).json()

    def patch(self, path: str, current: Json, body: Json) -> Json:
        """Update `current`, the resource as last read, under its ETag."""
        headers = {"if-match": f'"{current["id"]}:{current["version"]}"'}
        return self._send("PATCH", path, json=body, headers=headers).json()

    def sealed_run(self, workspace: str, run_id: str, timeout: float = 60) -> Json:
        deadline = time.monotonic() + timeout
        while (run := self.get(f"{workspace}/runs/{run_id}"))["status"] not in SEALED:
            if time.monotonic() > deadline:
                raise ApiError(f"Run {run_id} is still {run['status']} after {timeout:.0f}s")
            time.sleep(0.2)
        return run

    def _send(self, method: str, path: str, **options: Any) -> httpx2.Response:
        response = self._http.request(method, path, **options)
        if response.is_error:
            # Service error envelopes never echo submitted values.
            raise ApiError(f"{method} {path} failed ({response.status_code}): {response.text[:1000]}")
        return response

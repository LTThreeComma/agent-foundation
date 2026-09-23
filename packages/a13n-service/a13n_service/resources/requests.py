"""The assembled runtime as resource routes use it, as one typed FastAPI dependency.

It extends the tenancy runtime with what resources add; `app.py` stores the runs layer's `Runtime`, which is both.
"""

from typing import Annotated, Protocol

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from fastapi import Depends, Request

from a13n_service.providers.registry import Registry
from a13n_service.tenancy import requests


class Runtime(requests.Runtime, Protocol):
    @property
    def registry(self) -> Registry: ...

    @property
    def plugins(self) -> HarnessPluginFactoryCatalog: ...

    @property
    def endpoint_policy(self) -> EndpointPolicy: ...


async def current_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


CurrentRuntime = Annotated[Runtime, Depends(current_runtime)]

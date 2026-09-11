"""Shared value constraints for built-in Connector Provider configurations."""

from typing import Annotated

from pydantic import AfterValidator, Field

from ..domain import StrictModel
from ..validation import normalized_endpoint

Endpoint = Annotated[str, Field(min_length=1, max_length=2048), AfterValidator(normalized_endpoint)]


class ApiKeyCredentials(StrictModel):
    api_key: str = Field(
        title="API Key",
        min_length=1,
        max_length=4096,
        repr=False,
        json_schema_extra={"writeOnly": True, "format": "password"},
    )

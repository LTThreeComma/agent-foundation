"""The native settings each calling API accepts, as one JSON Schema the Console edits and the Service checks.

The schema is derived from the Pydantic AI settings type the Harness API names, so it cannot drift from what
execution passes on. Members JSON cannot carry, such as an `httpx.Timeout` object, are left out, and so are the
members that would let settings escape what the model and its provider resource decide.
"""

from collections.abc import Mapping
from functools import cache
from typing import Any, get_type_hints

from a13n_harness.providers.model.apis import MODEL_APIS
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from pydantic import ConfigDict, JsonValue, create_model
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaValue
from pydantic_core import PydanticOmit, core_schema

from a13n_service.infra.errors import invalid

# Bedrock's settings name boto3 request shapes that exist only for type checking; they are JSON objects.
_TYPE_CHECKING_ONLY: dict[str, object] = dict.fromkeys(
    (
        "GuardrailConfigurationTypeDef",
        "PerformanceConfigurationTypeDef",
        "PromptVariableValuesTypeDef",
        "ServiceTierTypeDef",
    ),
    dict[str, JsonValue],
)

_EXCLUDED = frozenset(
    {
        # Transport: the provider resource owns the request's headers and the operator its timeout; a raw body
        # or request-field passthrough could name another upstream model.
        "extra_body",
        "extra_headers",
        "timeout",
        "bedrock_additional_model_requests_fields",
        # Upstream selection: the model's `model_name` alone names what its provider's credential pays for.
        "bedrock_inference_profile",
        "openrouter_models",
        "openrouter_preset",
        # Account state: server-side conversations, containers and caches of the organization's provider account.
        "anthropic_container",
        "google_cached_content",
        "openai_conversation_id",
        "openai_previous_response_id",
        # Server-side tools: they search the account's vector stores or the web and run computer use, billed per
        # call outside token pricing and `max_usage`.
        "openai_native_tools",
    }
)


class _PortableSchema(GenerateJsonSchema):
    def handle_invalid_for_json_schema(self, schema: object, error_info: str) -> JsonSchemaValue:
        raise PydanticOmit

    def default_schema(self, schema: core_schema.WithDefaultSchema) -> JsonSchemaValue:
        # Every setting is optional and an absent one keeps the provider's behaviour, so none states a default.
        return self.generate_inner(schema["schema"])


@cache
def settings_schema(model_api: str) -> dict[str, JsonValue]:
    """Derive the schema. This imports the API's SDK, so the registry does it when it is assembled."""
    native = MODEL_APIS[model_api].settings_type
    fields: dict[str, Any] = {
        name: (hint, None)
        for name, hint in get_type_hints(native, localns=_TYPE_CHECKING_ONLY).items()
        if name not in _EXCLUDED
    }
    settings = create_model(
        native.__name__, __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True), **fields
    )
    return settings.model_json_schema(schema_generator=_PortableSchema)


def check_settings(schema: Mapping[str, JsonValue], settings: Mapping[str, JsonValue], *, field: str) -> None:
    """Refuse settings the schema does not accept, naming the most relevant failing path under `field`."""
    error = best_match(Draft202012Validator(schema).iter_errors(settings))
    if error is not None:
        raise invalid(".".join((field, *map(str, error.absolute_path))), error.message)

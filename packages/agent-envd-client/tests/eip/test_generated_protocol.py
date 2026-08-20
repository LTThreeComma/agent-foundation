from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from converge_agent_envd_client.eip.v1 import (
    EIP_DESCRIPTOR_SHA256,
    EIP_PROTO_PACKAGE,
    EIP_PROTOCOL_VERSION,
    METHODS,
    NOTIFICATION_METHODS,
    REQUEST_METHODS,
    JsonRpcRequest,
    decode_model,
    encode_model,
)
from converge_agent_envd_client.eip.v1.models import (
    CommandNetwork,
    EIPCallContext,
    EIPError,
    EncodedBytes,
    EnvironmentChangedNotification,
    FileStatParams,
    FileStatResult,
    InitializeParams,
    OutputReadParams,
    ProcessWriteStdinParams,
    SessionExpiringNotification,
    ShellExecParams,
    StateExportParams,
)
from pydantic import BaseModel, ValidationError

REPOSITORY_ROOT = Path(__file__).parents[4]
DESCRIPTOR_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/descriptor.pb"
GOLDEN_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/testdata/golden.json"
MODEL_TYPES: dict[str, type[BaseModel]] = {
    "InitializeParams": InitializeParams,
    "EnvironmentChangedNotification": EnvironmentChangedNotification,
    "FileStatParams": FileStatParams,
    "FileStatResult": FileStatResult,
    "ShellExecParams": ShellExecParams,
    "OutputReadParams": OutputReadParams,
    "ProcessWriteStdinParams": ProcessWriteStdinParams,
    "StateExportParams": StateExportParams,
    "SessionExpiringNotification": SessionExpiringNotification,
    "EIPError": EIPError,
}


def test_generated_surface_covers_eip_v1() -> None:
    assert EIP_PROTOCOL_VERSION == "1.0"
    assert EIP_PROTO_PACKAGE == "converge.agent_envd.eip.v1"
    assert len(METHODS) == 38
    assert len(REQUEST_METHODS) == 34
    assert len(NOTIFICATION_METHODS) == 4
    assert len(set(METHODS)) == len(METHODS)
    assert all(method.name == name for name, method in METHODS.items())
    assert all(method.introduced == "1.0" for method in METHODS.values())


def test_embedded_descriptor_digest_matches_checked_descriptor() -> None:
    assert hashlib.sha256(DESCRIPTOR_PATH.read_bytes()).hexdigest() == EIP_DESCRIPTOR_SHA256


def test_shared_golden_values_round_trip_canonically() -> None:
    fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        model_type = MODEL_TYPES[case["type"]]
        encoded_fixture = json.dumps(case["value"], separators=(",", ":"), sort_keys=True)
        model = decode_model(encoded_fixture, model_type)
        assert json.loads(encode_model(model)) == case["value"]


def test_explicit_wire_defaults_are_applied_but_omitted_canonically() -> None:
    params = ShellExecParams.model_validate(
        {
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {"kind": "argv", "executable": "true"},
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "output_policy": {"max_inline_bytes": 1, "max_output_bytes": 1, "overflow": "truncate"},
            },
        }
    )

    assert params.request.network is CommandNetwork.CONFIGURED
    assert params.request.environment.set == {}
    assert params.request.environment.unset == ()
    assert params.request.limits.wall_time_ms is None
    encoded_request = json.loads(encode_model(params))["request"]
    assert "network" not in encoded_request
    assert "environment" not in encoded_request
    assert "limits" not in encoded_request


def test_jsonrpc_envelope_ignores_extensions_but_reserves_eip_namespace() -> None:
    request = JsonRpcRequest.model_validate(
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": "environment.describe",
            "params": {},
            "trace_context": "optional-extension",
        }
    )
    assert request.id == "request-1"

    with pytest.raises(ValidationError, match="unknown reserved JSON-RPC field"):
        JsonRpcRequest.model_validate(
            {
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "environment.describe",
                "params": {},
                "eip_authority": "unexpected",
            }
        )
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({"jsonrpc": "2.0", "id": True, "method": "environment.describe", "params": {}})


def test_decoder_rejects_duplicate_and_unknown_authority_fields() -> None:
    payload = '{"context":{"operation_id":"one","operation_id":"two"},"path":{"mount_id":"workspace","path":"/repo"}}'
    with pytest.raises(ValueError, match="duplicate JSON field"):
        decode_model(payload, FileStatParams)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        decode_model(
            '{"context":{"operation_id":"one","principal":"caller"},"path":{"mount_id":"workspace","path":"/repo"}}',
            FileStatParams,
        )


def test_eip_profile_rejects_out_of_range_numbers_and_non_utc_timestamps() -> None:
    with pytest.raises(ValidationError):
        EIPError.model_validate(
            {
                "code": 2**31,
                "message": "invalid",
                "data": {"error_type": "internal_error", "retry_hint": "never", "dispatch_stage": "pre_dispatch"},
            }
        )
    with pytest.raises(ValidationError):
        OutputReadParams.model_validate(
            {
                "context": {"operation_id": "op"},
                "reference": "output-1",
                "start_offset": 2**64,
                "output_policy": {"max_inline_bytes": 1, "max_output_bytes": 1, "overflow": "truncate"},
            }
        )
    with pytest.raises(ValidationError):
        EIPCallContext.model_validate({"operation_id": "op", "deadline": 0})
    with pytest.raises(ValidationError):
        EIPCallContext.model_validate({"operation_id": "op", "deadline": "0"})
    with pytest.raises(ValidationError):
        EIPCallContext.model_validate({"operation_id": "op", "deadline": "2026-08-20T14:00:00"})

    context = EIPCallContext.model_validate({"operation_id": "op", "deadline": "2026-08-20T14:00:00.123456789Z"})
    assert context.deadline == "2026-08-20T14:00:00.123456789Z"
    assert json.loads(encode_model(context))["deadline"] == "2026-08-20T14:00:00.123456789Z"


def test_eip_profile_rejects_noncanonical_paths_and_base64() -> None:
    with pytest.raises(ValidationError):
        FileStatParams.model_validate(
            {"context": {"operation_id": "op"}, "path": {"mount_id": "workspace", "path": "/repo/../secret"}}
        )
    with pytest.raises(ValidationError):
        EncodedBytes(encoding="base64", data="aGVsbG8=")
    with pytest.raises(ValidationError):
        EncodedBytes(encoding="base64", data="AB")


def test_exactly_one_output_position_is_required() -> None:
    base = {
        "context": {"operation_id": "op"},
        "reference": "output-1",
        "output_policy": {"max_inline_bytes": 1, "max_output_bytes": 1, "overflow": "truncate"},
    }
    with pytest.raises(ValidationError, match="exactly one"):
        OutputReadParams.model_validate(base)
    with pytest.raises(ValidationError, match="exactly one"):
        OutputReadParams.model_validate({**base, "cursor": "cursor-1", "start_offset": 0})

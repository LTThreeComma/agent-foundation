from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from converge_agent_envd_client.eip.v1 import (
    EIP_DESCRIPTOR_SHA256,
    EIP_ERROR_CODES,
    EIP_PROTO_PACKAGE,
    EIP_PROTOCOL_VERSION,
    METHODS,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcSuccessResponse,
    decode_model,
    encode_model,
)
from converge_agent_envd_client.eip.v1.models import (
    CommandEnvironment,
    CommandNetwork,
    EIPCallContext,
    EIPError,
    EIPLimits,
    EncodedBytes,
    ErrorType,
    FileFindParams,
    FileStatParams,
    FileStatResult,
    InitializeParams,
    OutputCapture,
    OutputReadParams,
    ProcessWriteStdinParams,
    ReceiptGetParams,
    ShellExecParams,
)
from pydantic import BaseModel, ValidationError

REPOSITORY_ROOT = Path(__file__).parents[4]
DESCRIPTOR_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/descriptor.pb"
GOLDEN_PATH = REPOSITORY_ROOT / "crates/agent-envd/protocol/eip/v1/testdata/golden.json"


def valid_eip_limits() -> dict[str, int]:
    return {
        "max_request_bytes": 1,
        "max_response_bytes": 1,
        "max_concurrent_operations": 1,
        "max_processes": 1,
        "max_operation_duration_ms": 1,
        "max_inline_output_bytes": 1,
        "max_output_bytes": 1,
        "max_retained_bytes": 1,
        "max_retained_objects": 1,
        "max_retention_ttl_ms": 1,
        "max_operation_records": 1,
        "operation_record_ttl_ms": 1,
        "session_idle_ttl_ms": 1,
        "max_process_records": 1,
        "terminal_process_record_ttl_ms": 1,
    }


MODEL_TYPES: dict[str, type[BaseModel]] = {
    "InitializeParams": InitializeParams,
    "FileFindParams": FileFindParams,
    "FileStatParams": FileStatParams,
    "FileStatResult": FileStatResult,
    "ShellExecParams": ShellExecParams,
    "OutputReadParams": OutputReadParams,
    "ProcessWriteStdinParams": ProcessWriteStdinParams,
    "ReceiptGetParams": ReceiptGetParams,
    "EIPError": EIPError,
}


def test_generated_surface_covers_eip_v1() -> None:
    assert EIP_PROTOCOL_VERSION == "1.0"
    assert EIP_PROTO_PACKAGE == "converge.agent_envd.eip.v1"
    assert len(METHODS) == 30
    assert len(set(METHODS)) == len(METHODS)
    assert all(method.kind == "request_response" for method in METHODS.values())
    assert all(method.name == name for name, method in METHODS.items())
    assert all(method.introduced == "1.0" for method in METHODS.values())
    assert EIP_ERROR_CODES[ErrorType.RETENTION_GAP] == -32022


def test_embedded_descriptor_digest_matches_checked_descriptor() -> None:
    assert hashlib.sha256(DESCRIPTOR_PATH.read_bytes()).hexdigest() == EIP_DESCRIPTOR_SHA256


def test_shared_golden_values_round_trip_canonically() -> None:
    fixture = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        model_type = MODEL_TYPES[case["type"]]
        encoded_fixture = json.dumps(case["value"], separators=(",", ":"), sort_keys=True)
        model = decode_model(encoded_fixture, model_type)
        encoded_model = encode_model(model)
        assert encoded_model == encoded_fixture.encode()
        assert json.loads(encoded_model) == case["value"]


def test_encoder_revalidates_mutated_collection_values() -> None:
    environment = CommandEnvironment(set={"A": "valid"})
    environment.set["A"] = cast(str, 1)
    with pytest.raises(ValueError):
        encode_model(environment)


def test_explicit_wire_defaults_are_applied_but_omitted_canonically() -> None:
    params = ShellExecParams.model_validate(
        {
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {"kind": "argv", "executable": "true", "arguments": []},
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "environment": {"set": {}, "unset": []},
                "network": "configured",
                "limits": {},
                "keep_stdin_open": False,
            },
        }
    )

    assert params.request.network is CommandNetwork.CONFIGURED
    assert params.request.environment.set == {}
    assert params.request.environment.unset == ()
    assert params.request.limits.wall_time_ms is None
    assert params.request.output_policy is None
    encoded_request = json.loads(encode_model(params))["request"]
    assert encoded_request["command"] == {"executable": "true", "kind": "argv"}
    assert "network" not in encoded_request
    assert "environment" not in encoded_request
    assert "limits" not in encoded_request
    assert "keep_stdin_open" not in encoded_request
    assert "output_policy" not in encoded_request


def test_eip_limits_define_a_valid_omitted_output_policy() -> None:
    limits = valid_eip_limits()
    assert EIPLimits.model_validate(limits).max_output_bytes == 1

    with pytest.raises(ValidationError):
        EIPLimits.model_validate({**limits, "max_request_bytes": 0})
    with pytest.raises(ValidationError, match="max_inline_output_bytes cannot exceed"):
        EIPLimits.model_validate({**limits, "max_inline_output_bytes": 2})
    with pytest.raises(ValidationError, match="max_processes cannot exceed"):
        EIPLimits.model_validate({**limits, "max_processes": 2})
    with pytest.raises(ValidationError, match="max_concurrent_operations cannot exceed"):
        EIPLimits.model_validate({**limits, "max_concurrent_operations": 2})


def test_jsonrpc_integer_ids_use_signed_64_bit_range() -> None:
    request = {"jsonrpc": "2.0", "id": 2**63 - 1, "method": "environment.describe", "params": {}}
    assert JsonRpcRequest.model_validate(request).id == 2**63 - 1
    with pytest.raises(ValidationError):
        JsonRpcRequest.model_validate({**request, "id": 2**63})

    success = {"jsonrpc": "2.0", "id": -(2**63), "result": {}}
    assert JsonRpcSuccessResponse.model_validate(success).id == -(2**63)
    with pytest.raises(ValidationError):
        JsonRpcSuccessResponse.model_validate({**success, "id": -(2**63) - 1})

    error = {
        "jsonrpc": "2.0",
        "id": 2**63 - 1,
        "error": {
            "code": -32603,
            "message": "internal error",
            "data": {"error_type": "internal_error", "retry_hint": "never", "dispatch_stage": "unknown"},
        },
    }
    assert JsonRpcErrorResponse.model_validate(error).id == 2**63 - 1
    with pytest.raises(ValidationError):
        JsonRpcErrorResponse.model_validate({**error, "id": 2**63})
    with pytest.raises(ValidationError):
        JsonRpcErrorResponse.model_validate({key: value for key, value in error.items() if key != "id"})


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
    assert "trace_context" not in json.loads(encode_model(request))

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
                "code": -32603,
                "message": "invalid generation",
                "data": {
                    "error_type": "internal_error",
                    "retry_hint": "never",
                    "dispatch_stage": "pre_dispatch",
                    "generation": 0,
                },
            }
        )
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


def test_output_capture_rejects_unusable_structural_states() -> None:
    base = {
        "kind": "retained",
        "producer_complete": True,
        "content_complete": True,
        "produced_bytes": 1,
        "captured_bytes": 1,
        "dropped_bytes": 0,
        "available_start": 0,
        "available_end": 1,
    }
    with pytest.raises(ValidationError, match="requires a reference"):
        OutputCapture.model_validate(base)
    retained = OutputCapture.model_validate(
        {
            **base,
            "reference": "output-1",
            "expires_at": "2026-08-20T14:00:00Z",
        }
    )
    assert retained.reference is not None
    with pytest.raises(ValidationError, match="empty output"):
        OutputCapture.model_validate(
            {
                **base,
                "kind": "empty",
                "produced_bytes": 1,
                "captured_bytes": 0,
                "available_end": 0,
            }
        )
    with pytest.raises(ValidationError, match="available_start cannot exceed"):
        OutputCapture.model_validate({**base, "available_start": 2, "available_end": 1})


def test_receipt_lookup_requires_exactly_one_selector() -> None:
    base = {"context": {"operation_id": "op-query"}}
    with pytest.raises(ValidationError, match="exactly one"):
        ReceiptGetParams.model_validate(base)
    with pytest.raises(ValidationError, match="exactly one"):
        ReceiptGetParams.model_validate({**base, "receipt_ref": "receipt-1", "operation_id": "op-target"})

    assert ReceiptGetParams.model_validate({**base, "operation_id": "op-target"}).operation_id == "op-target"


def test_error_code_and_retention_gap_bounds_are_structural() -> None:
    common = {"retry_hint": "never", "dispatch_stage": "completed"}
    with pytest.raises(ValidationError, match="does not match error_type"):
        EIPError.model_validate(
            {
                "code": -32060,
                "message": "wrong",
                "data": {**common, "error_type": "retention_gap", "available_start": 0, "available_end": 1},
            }
        )
    with pytest.raises(ValidationError, match="requires available bounds"):
        EIPError.model_validate({"code": -32022, "message": "gap", "data": {**common, "error_type": "retention_gap"}})
    with pytest.raises(ValidationError, match="cannot exceed"):
        EIPError.model_validate(
            {
                "code": -32022,
                "message": "gap",
                "data": {**common, "error_type": "retention_gap", "available_start": 2, "available_end": 1},
            }
        )


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

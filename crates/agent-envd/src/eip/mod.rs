mod generated {
    #![allow(clippy::all, unused_variables)]

    include!(concat!(env!("OUT_DIR"), "/eip_wire.rs"));
}

pub use generated::*;

#[cfg(test)]
mod tests {
    use serde::{Serialize, de::DeserializeOwned};

    use super::{
        CommandNetwork, EIP_DESCRIPTOR_SHA256, EIP_PROTO_PACKAGE, EIP_PROTOCOL_VERSION,
        EIPCallContext, EIPError, EIPLimits, EIPServerInfo, EipValidate, ErrorType, FileFindParams,
        FileStatParams, FileStatResult, InitializeParams, JsonRpcErrorResponse, JsonRpcRequest,
        JsonRpcSuccessResponse, METHODS, OutputCapture, OutputOverflow, OutputPolicy,
        OutputReadParams, ProcessWriteStdinParams, ReceiptGetParams, ShellExecParams, decode,
        encode,
    };

    fn assert_golden<T>(value: serde_json::Value)
    where
        T: DeserializeOwned + Serialize + EipValidate,
    {
        let payload = serde_json::to_string(&value).expect("fixture is JSON");
        let decoded: T = decode(&payload).expect("fixture follows EIP profile");
        let encoded = encode(&decoded).expect("model encodes canonically");
        assert_eq!(
            encoded,
            serde_json::to_vec(&value).expect("fixture encodes")
        );
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            value
        );
    }

    #[test]
    fn generated_registry_has_complete_v1_surface() {
        assert_eq!(EIP_PROTOCOL_VERSION, "1.0");
        assert_eq!(EIP_PROTO_PACKAGE, "converge.agent_envd.eip.v1");
        assert_eq!(METHODS.len(), 30);
        assert!(
            METHODS
                .iter()
                .all(|method| method.kind == "request_response")
        );
        assert_eq!(EIP_DESCRIPTOR_SHA256.len(), 64);
        assert_eq!(ErrorType::RetentionGap.code(), -32022);
    }

    #[test]
    fn generated_registry_has_unique_jsonrpc_names() {
        let mut names = METHODS.iter().map(|method| method.name).collect::<Vec<_>>();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), METHODS.len());
    }

    #[test]
    fn rust_models_match_shared_golden_values() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../protocol/eip/v1/testdata/golden.json"))
                .expect("golden fixture is valid JSON");
        for case in fixture["cases"].as_array().expect("cases is an array") {
            let value = case["value"].clone();
            match case["type"].as_str().expect("case type is a string") {
                "InitializeParams" => assert_golden::<InitializeParams>(value),
                "FileFindParams" => assert_golden::<FileFindParams>(value),
                "FileStatParams" => assert_golden::<FileStatParams>(value),
                "FileStatResult" => assert_golden::<FileStatResult>(value),
                "ShellExecParams" => assert_golden::<ShellExecParams>(value),
                "OutputReadParams" => assert_golden::<OutputReadParams>(value),
                "ProcessWriteStdinParams" => assert_golden::<ProcessWriteStdinParams>(value),
                "ReceiptGetParams" => assert_golden::<ReceiptGetParams>(value),
                "EIPError" => assert_golden::<EIPError>(value),
                other => panic!("unhandled golden fixture type: {other}"),
            }
        }
    }

    #[test]
    fn rust_models_apply_and_canonically_omit_explicit_defaults() {
        let value = serde_json::json!({
            "context": {"operation_id": "op-defaults"},
            "request": {
                "command": {"kind": "argv", "executable": "true", "arguments": []},
                "cwd": {"mount_id": "workspace", "path": "/repo"},
                "environment": {"set": {}, "unset": []},
                "network": "configured",
                "limits": {},
                "keep_stdin_open": false
            }
        });
        let payload = serde_json::to_string(&value).expect("fixture is JSON");

        let decoded: ShellExecParams = decode(&payload).expect("fixture follows EIP profile");

        assert_eq!(decoded.request.network, CommandNetwork::Configured);
        assert!(decoded.request.environment.set.is_empty());
        assert!(decoded.request.environment.unset.is_empty());
        assert!(decoded.request.limits.wall_time_ms.is_none());
        assert_eq!(
            serde_json::to_value(decoded).expect("model serializes"),
            serde_json::json!({
                "context": {"operation_id": "op-defaults"},
                "request": {
                    "command": {"kind": "argv", "executable": "true"},
                    "cwd": {"mount_id": "workspace", "path": "/repo"}
                }
            })
        );
    }

    fn valid_eip_limits() -> serde_json::Value {
        serde_json::json!({
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
            "terminal_process_record_ttl_ms": 1
        })
    }

    #[test]
    fn rust_eip_limits_define_a_valid_omitted_output_policy() {
        let limits = valid_eip_limits();
        assert!(decode::<EIPLimits>(&limits.to_string()).is_ok());

        for (field, value) in [
            ("max_request_bytes", 0),
            ("max_inline_output_bytes", 2),
            ("max_processes", 2),
            ("max_concurrent_operations", 2),
        ] {
            let mut invalid = limits.clone();
            invalid[field] = value.into();
            assert!(decode::<EIPLimits>(&invalid.to_string()).is_err());
        }
    }

    #[test]
    fn rust_jsonrpc_integer_ids_use_signed_64_bit_range() {
        let request_max = r#"{"jsonrpc":"2.0","id":9223372036854775807,"method":"environment.describe","params":{}}"#;
        let request_over = r#"{"jsonrpc":"2.0","id":9223372036854775808,"method":"environment.describe","params":{}}"#;
        assert!(decode::<JsonRpcRequest>(request_max).is_ok());
        assert!(decode::<JsonRpcRequest>(request_over).is_err());

        let success_min = r#"{"jsonrpc":"2.0","id":-9223372036854775808,"result":{}}"#;
        let success_under = r#"{"jsonrpc":"2.0","id":-9223372036854775809,"result":{}}"#;
        assert!(decode::<JsonRpcSuccessResponse>(success_min).is_ok());
        assert!(decode::<JsonRpcSuccessResponse>(success_under).is_err());

        let error_max = r#"{"jsonrpc":"2.0","id":9223372036854775807,"error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        let error_over = r#"{"jsonrpc":"2.0","id":9223372036854775808,"error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        let error_missing_id = r#"{"jsonrpc":"2.0","error":{"code":-32603,"message":"internal error","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"unknown"}}}"#;
        assert!(decode::<JsonRpcErrorResponse>(error_max).is_ok());
        assert!(decode::<JsonRpcErrorResponse>(error_over).is_err());
        assert!(decode::<JsonRpcErrorResponse>(error_missing_id).is_err());
    }

    #[test]
    fn rust_generated_jsonrpc_envelope_rejects_reserved_extensions() {
        let valid = r#"{"jsonrpc":"2.0","id":"request-1","method":"environment.describe","params":{},"trace_context":"value"}"#;
        let decoded = decode::<JsonRpcRequest>(valid).expect("ordinary extensions are accepted");
        let reencoded = encode(&decoded).expect("envelope encodes");
        assert!(
            !String::from_utf8(reencoded)
                .expect("JSON is UTF-8")
                .contains("trace_context")
        );

        let reserved = r#"{"jsonrpc":"2.0","id":"request-1","method":"environment.describe","params":{},"eip_authority":"unexpected"}"#;
        assert!(decode::<JsonRpcRequest>(reserved).is_err());
    }

    #[test]
    fn rust_error_code_and_retention_bounds_are_structural() {
        let wrong_code = r#"{"code":-32060,"message":"wrong","data":{"error_type":"retention_gap","retry_hint":"never","dispatch_stage":"completed","available_start":0,"available_end":1}}"#;
        assert!(decode::<EIPError>(wrong_code).is_err());

        let missing_bounds = r#"{"code":-32022,"message":"gap","data":{"error_type":"retention_gap","retry_hint":"never","dispatch_stage":"completed"}}"#;
        assert!(decode::<EIPError>(missing_bounds).is_err());

        let reversed_bounds = r#"{"code":-32022,"message":"gap","data":{"error_type":"retention_gap","retry_hint":"never","dispatch_stage":"completed","available_start":2,"available_end":1}}"#;
        assert!(decode::<EIPError>(reversed_bounds).is_err());
    }

    #[test]
    fn rust_encoder_rejects_structurally_invalid_models() {
        assert!(
            encode(&EIPServerInfo {
                name: "not-agent-envd".to_owned(),
                version: "1.0.0".to_owned(),
            })
            .is_err()
        );
        assert!(
            encode(&OutputPolicy {
                max_inline_bytes: 2,
                max_output_bytes: 1,
                overflow: OutputOverflow::Truncate,
            })
            .is_err()
        );
    }

    #[test]
    fn rust_operation_ids_use_the_canonical_character_bound() {
        let maximum = "🧪".repeat(128);
        let valid = serde_json::json!({"operation_id": maximum});
        assert!(decode::<EIPCallContext>(&valid.to_string()).is_ok());

        let too_long = serde_json::json!({"operation_id": "🧪".repeat(129)});
        assert!(decode::<EIPCallContext>(&too_long.to_string()).is_err());
    }

    #[test]
    fn rust_decoder_rejects_profile_violations() {
        let zero_generation = r#"{"code":-32603,"message":"invalid generation","data":{"error_type":"internal_error","retry_hint":"never","dispatch_stage":"pre_dispatch","generation":0}}"#;
        assert!(decode::<EIPError>(zero_generation).is_err());

        let unknown = r#"{"context":{"operation_id":"op","principal":"caller"},"path":{"mount_id":"workspace","path":"/repo"}}"#;
        assert!(decode::<FileStatParams>(unknown).is_err());

        let invalid_path = r#"{"context":{"operation_id":"op"},"path":{"mount_id":"workspace","path":"/repo/../secret"}}"#;
        assert!(decode::<FileStatParams>(invalid_path).is_err());

        let retained_without_reference = r#"{"kind":"retained","producer_complete":true,"content_complete":true,"produced_bytes":1,"captured_bytes":1,"dropped_bytes":0,"available_start":0,"available_end":1}"#;
        assert!(decode::<OutputCapture>(retained_without_reference).is_err());
        let valid_retained = r#"{"kind":"retained","producer_complete":true,"content_complete":true,"produced_bytes":1,"captured_bytes":1,"dropped_bytes":0,"reference":"output-1","available_start":0,"available_end":1,"expires_at":"2026-08-20T14:00:00Z"}"#;
        assert!(decode::<OutputCapture>(valid_retained).is_ok());
        let empty_with_bytes = r#"{"kind":"empty","producer_complete":true,"content_complete":true,"produced_bytes":1,"captured_bytes":0,"dropped_bytes":1,"available_start":0,"available_end":0}"#;
        assert!(decode::<OutputCapture>(empty_with_bytes).is_err());
        let reversed_available = r#"{"kind":"retained","producer_complete":true,"content_complete":true,"produced_bytes":1,"captured_bytes":1,"dropped_bytes":0,"available_start":2,"available_end":1}"#;
        assert!(decode::<OutputCapture>(reversed_available).is_err());

        let missing_position = r#"{"context":{"operation_id":"op"},"reference":"output-1"}"#;
        assert!(decode::<OutputReadParams>(missing_position).is_err());

        let missing_receipt_selector = r#"{"context":{"operation_id":"op-query"}}"#;
        assert!(decode::<ReceiptGetParams>(missing_receipt_selector).is_err());
        let duplicate_receipt_selector = r#"{"context":{"operation_id":"op-query"},"receipt_ref":"receipt-1","operation_id":"op-target"}"#;
        assert!(decode::<ReceiptGetParams>(duplicate_receipt_selector).is_err());

        let duplicate_map_key = r#"{"context":{"operation_id":"op"},"request":{"command":{"kind":"argv","executable":"true"},"cwd":{"mount_id":"workspace","path":"/repo"},"environment":{"set":{"PATH":"one","PATH":"two"}},"output_policy":{"max_inline_bytes":1,"max_output_bytes":1,"overflow":"truncate"}}}"#;
        assert!(decode::<ShellExecParams>(duplicate_map_key).is_err());
    }
}
